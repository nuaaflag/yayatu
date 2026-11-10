// YayaTuPro 单文件自解压引导器 (SFX Launcher)
// 目标框架: .NET Framework 4.x  (Windows 10/11 自带)
// 编译: csc /target:winexe /platform:x64 /optimize+ /win32icon:app.ico Sfx.cs
//
// 文件布局:
//   [0 .. P)          PE 引导器本体
//   [P .. P+L)        zip 载荷（dist_http/YayaTuPro 的全部内容）
//   [P+L .. P+L+8)    int64 LE = L
//   [.. +10)          魔数 "YAYATUSFX1"
//
// 运行时行为:
//   1. 首次运行 → 解压到 %LOCALAPPDATA%\YayaTuPro
//   2. 后续运行 → 版本戳一致则跳过解压，直接启动
//   3. 版本变化（换了新包）→ 重新解压覆盖

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace YayaTuSfx
{
    internal static class Program
    {
        private const string TailMagic = "YAYATUSFX1";
        private const int  TailMagicLen = 10;
        private const int  TailFieldLen = 8 + TailMagicLen;

        private const string AppFolder  = "YayaTuPro";
        private const string AppExeName = "YayaTuPro.exe";
        private const string StampName  = ".sfx-version";
        private const string MutexName  = "Global\\YayaTuPro.Sfx.Launcher";

        private static string _logPath;
        private static string LogPath
        {
            get
            {
                if (_logPath != null) return _logPath;
                foreach (string c in CandidateDirs())
                {
                    try
                    {
                        Directory.CreateDirectory(c);
                        string p = Path.Combine(c, "YayaTuPro_sfx.log");
                        using (FileStream fs = new FileStream(p, FileMode.Append, FileAccess.Write))
                        { _logPath = p; return _logPath; }
                    }
                    catch { }
                }
                _logPath = "";
                return _logPath;
            }
        }

        private static void Log(string fmt, params object[] a)
        {
            try
            {
                string lp = LogPath;
                if (lp.Length == 0) return;
                string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " +
                              (a == null || a.Length == 0 ? fmt : string.Format(fmt, a));
                File.AppendAllText(lp, line + Environment.NewLine, new UTF8Encoding(false));
            }
            catch { }
        }

        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Log("=== sfx start, exe={0}", Application.ExecutablePath);
            AppDomain.CurrentDomain.UnhandledException += delegate(object s2, UnhandledExceptionEventArgs e2)
            {
                Log("UNHANDLED: {0}", e2.ExceptionObject);
            };

            string self = Application.ExecutablePath;

            long off, len;
            Log("tail check: self={0}", self);
            if (!ReadTail(self, out off, out len))
            {
                Log("tail check FAILED");
                ShowError("单文件包已损坏：找不到内置数据段。\n\n文件：" + self);
                return 2;
            }

            string rootNote;
            string root    = ResolveRoot(out rootNote);
            if (root == null)
            {
                ShowError("无法找到可写的运行目录。\n\n已尝试：\n" +
                          "  · %LOCALAPPDATA%\\" + AppFolder + "\n" +
                          "  · %TEMP%\\" + AppFolder + "\n" +
                          "  · 程序所在目录\n\n" +
                          "请把本程序放到有写权限的目录（例如桌面）后重试。");
                return 4;
            }
            Log("resolved root={0} ({1})", root, rootNote);
            string exePath = Path.Combine(root, AppExeName);
            string stamp   = Path.Combine(root, StampName);
            string version = MakeVersionStamp(self, off, len);

            Log("root={0} version={1}", root, version);

            if (IsUpToDate(exePath, stamp, version))
            {
                Log("up-to-date, starting directly");
                StartApp(exePath, root, args);
                return 0;
            }

            Log("needs extraction");
            bool owned;
            using (Mutex gate = new Mutex(true, MutexName, out owned))
            {
                if (!owned)
                {
                    // 另一个实例正在解包，等它完成
                    try { gate.WaitOne(TimeSpan.FromMinutes(5)); }
                    catch (AbandonedMutexException) { }
                    if (IsUpToDate(exePath, stamp, version))
                    {
                        StartApp(exePath, root, args);
                        return 0;
                    }
                }

                Log("acquired mutex (owned={0})", owned);
                string err = null;
                string detail = "";
                string stage = root + ".staging";
                try
                {
                    Log("extracting to {0}", stage);
                    RunWithSplash(delegate(Splash s)
                    {
                        SafeDeleteDir(stage);
                        Log("extract begin off={0} len={1}", off, len);
                        ExtractZip(self, off, len, stage, s);
                        Log("extract done");
                        SafeDeleteDir(root);
                        Log("old root removed, moving staging");
                        Directory.Move(stage, root);
                        Log("moved");
                        File.WriteAllText(stamp, version, Encoding.UTF8);
                        Log("stamp written");
                    });
                    Log("extraction pipeline OK");
                }
                catch (Exception ex)
                {
                    err = ex.Message;
                    detail = ex.ToString();
                    Log("EXTRACT FAILED: {0}", detail);
                }

                if (err != null)
                {
                    SafeDeleteDir(stage);
                    ShowError("释放运行文件失败：\n\n" + err + "\n\n" + detail +
                              "\n\n目标目录：" + root +
                              "\n\n如果程序正在运行，请先退出后重试。");
                    return 3;
                }

                if (owned) { try { gate.ReleaseMutex(); } catch { } }
            }

            Log("launching {0}", exePath);
            StartApp(exePath, root, args);
            Log("launched, sfx exit");
            return 0;
        }

        // ---------------- 运行目录选择 ----------------

        private static string[] CandidateDirs()
        {
            System.Collections.Generic.List<string> list =
                new System.Collections.Generic.List<string>();

            try
            {
                string lad = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                if (!string.IsNullOrEmpty(lad)) list.Add(lad);
            }
            catch { }

            try
            {
                string tmp = Path.GetTempPath();
                if (!string.IsNullOrEmpty(tmp) &&
                    (list.Count == 0 || !string.Equals(Path.GetFullPath(tmp).TrimEnd('\\'),
                                                      Path.GetFullPath(list[0]).TrimEnd('\\'),
                                                      StringComparison.OrdinalIgnoreCase)))
                    list.Add(tmp);
            }
            catch { }

            try
            {
                string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
                if (!string.IsNullOrEmpty(exeDir)) list.Add(exeDir);
            }
            catch { }

            return list.ToArray();
        }

        private static bool CanWrite(string dir)
        {
            try
            {
                Directory.CreateDirectory(dir);
                string probe = Path.Combine(dir, ".ytp-" + Guid.NewGuid().ToString("N").Substring(0, 8));
                using (FileStream fs = new FileStream(probe, FileMode.CreateNew, FileAccess.Write))
                    fs.WriteByte(7);
                File.Delete(probe);
                return true;
            }
            catch { return false; }
        }

        private static string ResolveRoot(out string note)
        {
            // 1. 已解包过的位置（避免重复解压）
            foreach (string c in CandidateDirs())
            {
                try
                {
                    string dir = Path.Combine(c, AppFolder);
                    string stamp = Path.Combine(dir, StampName);
                    string exe = Path.Combine(dir, AppExeName);
                    if (File.Exists(stamp) && File.Exists(exe) && CanWrite(dir))
                    {
                        note = "existing";
                        return dir;
                    }
                }
                catch { }
            }

            // 2. 首选 %LOCALAPPDATA%，其次 %TEMP%，最后 exe 同级
            foreach (string c in CandidateDirs())
            {
                try
                {
                    string dir = Path.Combine(c, AppFolder);
                    if (CanWrite(dir)) { note = "writable"; return dir; }
                }
                catch { }
            }

            note = "none";
            return null;
        }

        // ---------------- 尾部读取 ----------------

        private static bool ReadTail(string self, out long offset, out long length)
        {
            offset = 0;
            length = 0;
            try
            {
                using (FileStream fs = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (fs.Length < TailFieldLen + 22) return false;

                    byte[] tail = new byte[TailFieldLen];
                    fs.Position = fs.Length - TailFieldLen;
                    ReadFully(fs, tail, 0, tail.Length);

                    string magic = Encoding.ASCII.GetString(tail, 8, TailMagicLen);
                    if (magic != TailMagic) return false;

                    length = BitConverter.ToInt64(tail, 0);
                    offset = fs.Length - TailFieldLen - length;
                    if (length <= 0 || offset < 0) return false;

                    // 校验 zip 起始标记 "PK\003\004"
                    fs.Position = offset;
                    byte[] pk = new byte[4];
                    ReadFully(fs, pk, 0, 4);
                    return pk[0] == 0x50 && pk[1] == 0x4B && pk[2] == 0x03 && pk[3] == 0x04;
                }
            }
            catch { return false; }
        }

        private static void ReadFully(Stream s, byte[] buf, int off, int count)
        {
            int done = 0;
            while (done < count)
            {
                int n = s.Read(buf, off + done, count - done);
                if (n <= 0) throw new EndOfStreamException();
                done += n;
            }
        }

        // ---------------- 版本戳 ----------------

        private static string MakeVersionStamp(string self, long off, long len)
        {
            // 载荷长度 + 前 1MB 的 SHA1 + 尾部 64KB 的 SHA1，足以区分不同构建
            using (FileStream fs = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (SHA1 sha = SHA1.Create())
            {
                fs.Position = off;
                byte[] buf = new byte[1024 * 1024];
                int n = ReadAtMost(fs, buf);
                sha.TransformBlock(buf, 0, n, null, 0);

                if (len > 64 * 1024)
                {
                    fs.Position = off + len - 64 * 1024;
                    byte[] tail = new byte[64 * 1024];
                    int m = ReadAtMost(fs, tail);
                    sha.TransformBlock(tail, 0, m, null, 0);
                }

                sha.TransformFinalBlock(new byte[0], 0, 0);
                StringBuilder sb = new StringBuilder();
                sb.Append(len.ToString("x"));
                sb.Append('-');
                sb.Append(Convert.ToBase64String(sha.Hash).TrimEnd('=').Replace('/', '_').Replace('+', '-'));
                return sb.ToString();
            }
        }

        private static int ReadAtMost(Stream s, byte[] buf)
        {
            int done = 0;
            while (done < buf.Length)
            {
                int n = s.Read(buf, done, buf.Length - done);
                if (n <= 0) break;
                done += n;
            }
            return done;
        }

        private static bool IsUpToDate(string exePath, string stamp, string version)
        {
            try
            {
                if (!File.Exists(exePath) || !File.Exists(stamp)) return false;
                return File.ReadAllText(stamp, Encoding.UTF8).Trim() == version;
            }
            catch { return false; }
        }

        // ---------------- 解包 ----------------

        private static void ExtractZip(string self, long off, long len, string dest, Splash splash)
        {
            using (FileStream fs = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (SubStream sub = new SubStream(fs, off, len))
            using (ZipArchive zip = new ZipArchive(sub, ZipArchiveMode.Read))
            {
                int total = zip.Entries.Count;
                int cur = 0;
                string destFull = Path.GetFullPath(dest);
                if (!destFull.EndsWith(Path.DirectorySeparatorChar.ToString()))
                    destFull += Path.DirectorySeparatorChar;

                Directory.CreateDirectory(dest);

                foreach (ZipArchiveEntry e in zip.Entries)
                {
                    cur++;
                    if (splash != null) splash.SetProgress(cur * 1000 / (total > 0 ? total : 1),
                                                           Path.GetFileName(e.FullName));

                    string rel = e.FullName.Replace('/', Path.DirectorySeparatorChar);
                    if (rel.Length == 0) continue;

                    string target = Path.GetFullPath(Path.Combine(dest, rel));
                    if (!target.StartsWith(destFull, StringComparison.OrdinalIgnoreCase))
                        throw new IOException("非法路径条目: " + e.FullName);

                    if (rel.EndsWith(Path.DirectorySeparatorChar.ToString()))
                    {
                        Directory.CreateDirectory(target);
                        continue;
                    }

                    string dir = Path.GetDirectoryName(target);
                    if (dir != null && dir.Length > 0) Directory.CreateDirectory(dir);

                    using (Stream input = e.Open())
                    using (FileStream output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None, 65536))
                    {
                        byte[] buf = new byte[65536];
                        int n;
                        while ((n = input.Read(buf, 0, buf.Length)) > 0)
                            output.Write(buf, 0, n);
                    }

                    // 保留时间戳（不保留只读属性，避免后续覆盖失败）
                    try { File.SetLastWriteTimeUtc(target, e.LastWriteTime.UtcDateTime); }
                    catch { }
                }
            }
        }

        // ---------------- 启动 ----------------

        private static void StartApp(string exePath, string workDir, string[] args)
        {
            Log("StartApp: {0} cwd={1}", exePath, workDir);
            if (!File.Exists(exePath))
            {
                ShowError("找不到主程序：\n" + exePath);
                return;
            }
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exePath);
                psi.WorkingDirectory = workDir;
                psi.UseShellExecute = false;
                if (args != null && args.Length > 0)
                {
                    StringBuilder sb = new StringBuilder();
                    for (int i = 0; i < args.Length; i++)
                    {
                        if (i > 0) sb.Append(' ');
                        sb.Append('"').Append(args[i].Replace("\"", "\\\"")).Append('"');
                    }
                    psi.Arguments = sb.ToString();
                }
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                ShowError("启动失败：\n\n" + ex.Message);
            }
        }

        private static void RunWithSplash(Action<Splash> work)
        {
            Splash splash = new Splash();
            Exception error = null;

            Thread worker = new Thread(delegate()
            {
                try { work(splash); }
                catch (Exception ex) { error = ex; }
                finally
                {
                    try { splash.BeginInvoke(new Action(splash.Close)); }
                    catch { }
                }
            });
            worker.IsBackground = true;
            worker.Start();

            Application.Run(splash);
            worker.Join(3000);

            if (error != null) throw error;
        }

        // ---------------- 杂项 ----------------

        private static void SafeDeleteDir(string dir)
        {
            try
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, true);
            }
            catch
            {
                // 逐个删，跳过被占用的
                try
                {
                    foreach (string f in Directory.GetFiles(dir, "*", SearchOption.AllDirectories))
                    {
                        try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); }
                        catch { }
                    }
                    foreach (string d in Directory.GetDirectories(dir, "*", SearchOption.AllDirectories))
                    {
                        try { Directory.Delete(d, true); } catch { }
                    }
                    Directory.Delete(dir, true);
                }
                catch { }
            }
        }

        private static void ShowError(string msg)
        {
            MessageBox.Show(msg, "鸭鸭图 (SuperSecureMedia)",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        // ---------------- 只读 zip 子流 ----------------

        internal sealed class SubStream : Stream
        {
            private readonly Stream _base;
            private readonly long _start;
            private readonly long _length;
            private long _pos;

            public SubStream(Stream baseStream, long start, long length)
            {
                _base = baseStream;
                _start = start;
                _length = length;
            }

            public override bool CanRead  { get { return true; } }
            public override bool CanSeek  { get { return true; } }
            public override bool CanWrite { get { return false; } }
            public override long Length   { get { return _length; } }

            public override long Position
            {
                get { return _pos; }
                set { _pos = value; }
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                if (_pos >= _length) return 0;
                long avail = _length - _pos;
                if (count > avail) count = (int)avail;
                _base.Position = _start + _pos;
                int n = _base.Read(buffer, offset, count);
                if (n > 0) _pos += n;
                return n;
            }

            public override long Seek(long offset, SeekOrigin origin)
            {
                long target;
                switch (origin)
                {
                    case SeekOrigin.Begin:   target = offset; break;
                    case SeekOrigin.Current: target = _pos + offset; break;
                    default:                 target = _length + offset; break;
                }
                _pos = target;
                return _pos;
            }

            public override void Flush() { }
            public override void SetLength(long value) { throw new NotSupportedException(); }
            public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
        }

        // ---------------- 进度窗口 ----------------

        internal sealed class Splash : Form
        {
            private readonly ProgressBar _bar;
            private readonly Label _label;

            public Splash()
            {
                Text = "鸭鸭图 (SuperSecureMedia)";
                FormBorderStyle = FormBorderStyle.FixedDialog;
                MaximizeBox = false;
                MinimizeBox = false;
                ShowInTaskbar = true;
                StartPosition = FormStartPosition.CenterScreen;
                ClientSize = new Size(440, 116);
                BackColor = Color.White;

                Label title = new Label();
                title.Text = "首次启动，正在准备运行文件…";
                title.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
                title.SetBounds(16, 14, 408, 22);
                Controls.Add(title);

                _bar = new ProgressBar();
                _bar.SetBounds(16, 42, 408, 18);
                _bar.Minimum = 0;
                _bar.Maximum = 1000;
                Controls.Add(_bar);

                _label = new Label();
                _label.Text = "";
                _label.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular);
                _label.ForeColor = Color.Gray;
                _label.AutoEllipsis = true;
                _label.SetBounds(16, 68, 408, 18);
                Controls.Add(_label);

                Label hint = new Label();
                hint.Text = "仅首次启动需要，之后启动会更快。";
                hint.Font = new Font("Microsoft YaHei UI", 8F, FontStyle.Regular);
                hint.ForeColor = Color.Gray;
                hint.SetBounds(16, 88, 408, 18);
                Controls.Add(hint);
            }

            public void SetProgress(int perMille, string current)
            {
                if (IsDisposed) return;
                try
                {
                    if (InvokeRequired) { BeginInvoke(new Action<int, string>(SetProgress), perMille, current); return; }
                    if (perMille < 0) perMille = 0;
                    if (perMille > 1000) perMille = 1000;
                    _bar.Value = perMille;
                    _label.Text = current;
                }
                catch { }
            }
        }
    }
}
