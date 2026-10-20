using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace HookApp
{
    internal class MainForm : Form
    {
        /* ── 由生成器填充的参数 ── */
        private const string TargetUrl = "__TARGET_URL__";
        private const string AppTitle = "__APP_TITLE__";
        private const string DataName = "__DATA_NAME__";
        private static readonly bool TrayOnClose = __TRAY_CLOSE__;
        private static readonly string[] AllowedHosts = new string[]
        {
            __ALLOWED_HOSTS__
        };

        private readonly WebView2 webView;
        private NotifyIcon tray;
        private bool reallyExit;
        private bool balloonShown;
        private CoreWebView2 core;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private readonly string dataDir;

        public MainForm()
        {
            Text = AppTitle;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(1280, 840);
            MinimumSize = new Size(980, 620);
            Font = new Font("Microsoft YaHei UI", 9F);

            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch { Icon = SystemIcons.Application; }

            dataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "HookApp", DataName);

            webView = new WebView2();
            webView.Dock = DockStyle.Fill;
            Controls.Add(webView);

            Shown += MainForm_Shown;
            FormClosing += MainForm_FormClosing;
            FormClosed += MainForm_FormClosed;

            BuildTray();
        }

        /* ─────────────── 托盘 ─────────────── */

        private void BuildTray()
        {
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("显示主窗口", null, delegate { ShowWindow(); });
            menu.Items.Add("重新载入页面", null, delegate { NavigateHome(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("退出", null, delegate { ExitApp(); });

            tray = new NotifyIcon();
            tray.Icon = Icon;
            tray.Text = AppTitle.Length > 63 ? AppTitle.Substring(0, 63) : AppTitle;
            tray.ContextMenuStrip = menu;
            tray.Visible = true;
            tray.DoubleClick += delegate { ShowWindow(); };
        }

        private void ShowWindow()
        {
            Show();
            if (WindowState == FormWindowState.Minimized)
                WindowState = FormWindowState.Normal;
            Activate();
        }

        private void NavigateHome()
        {
            ShowWindow();
            if (core != null) core.Navigate(TargetUrl);
        }

        private void ExitApp()
        {
            reallyExit = true;
            if (tray != null) tray.Visible = false;
            Close();
        }

        private void MainForm_FormClosing(object sender, FormClosingEventArgs e)
        {
            // 按生成器配置：关闭窗口 = 收进托盘继续监听，或直接退出
            if (!reallyExit && TrayOnClose && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                if (!balloonShown)
                {
                    balloonShown = true;
                    tray.ShowBalloonTip(3000, AppTitle,
                        "已最小化到托盘，页面与脚本仍在后台运行。",
                        ToolTipIcon.Info);
                }
                return;
            }
        }

        private void MainForm_FormClosed(object sender, FormClosedEventArgs e)
        {
            if (tray != null) tray.Visible = false;
            try { webView.Dispose(); } catch { }
        }

        /* ─────────────── WebView2 初始化 ─────────────── */

        private async void MainForm_Shown(object sender, EventArgs e)
        {
            string browserData = Path.Combine(dataDir, "WebView2Data");
            try { Directory.CreateDirectory(browserData); }
            catch { }

            try
            {
                CoreWebView2Environment env =
                    await CoreWebView2Environment.CreateAsync(null, browserData, null);
                await webView.EnsureCoreWebView2Async(env);
            }
            catch (Exception ex)
            {
                LogHost("WebView2 init failed: " + ex.Message);
                MessageBox.Show(
                    "初始化 Edge WebView2 内核失败：\r\n\r\n" + ex.Message +
                    "\r\n\r\n请确认系统已安装 Microsoft Edge 或 WebView2 运行时。",
                    AppTitle, MessageBoxButtons.OK, MessageBoxIcon.Error);
                reallyExit = true;
                if (tray != null) tray.Visible = false;
                Close();
                return;
            }

            core = webView.CoreWebView2;

            // 允许 F12 开发者工具，方便调试注入的脚本
            try { core.Settings.AreDevToolsEnabled = true; } catch { }

            // 每次页面文档创建时自动注入用户脚本（GM 兼容层 + 油猴脚本本体）
            string hookJs = LoadHookJs();
            if (!string.IsNullOrEmpty(hookJs))
            {
                try
                {
                    await core.AddScriptToExecuteOnDocumentCreatedAsync(hookJs);
                }
                catch (Exception ex)
                {
                    LogHost("AddScript failed: " + ex.Message);
                }
            }

            core.WebMessageReceived += OnWebMessage;
            webView.Source = new Uri(TargetUrl);
        }

        private string LoadHookJs()
        {
            try
            {
                Assembly asm = Assembly.GetExecutingAssembly();
                string[] names = asm.GetManifestResourceNames();
                foreach (string n in names)
                {
                    if (n.EndsWith(".hook.js", StringComparison.OrdinalIgnoreCase))
                    {
                        using (Stream s = asm.GetManifestResourceStream(n))
                        using (StreamReader r = new StreamReader(s, Encoding.UTF8))
                            return r.ReadToEnd();
                    }
                }
                LogHost("hook.js resource not found. names=" + string.Join(",", names));
            }
            catch (Exception ex)
            {
                LogHost("LoadHookJs failed: " + ex.Message);
            }
            return null;
        }

        /* ─────────────── 页面消息桥 ─────────────── */

        private async void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                // 只信任白名单域名发来的桥消息，防止跳转到其他站点后被滥用
                string src = "";
                try
                {
                    if (e.Source != null) src = e.Source;
                }
                catch { }
                if (!IsAllowedOrigin(src)) return;

                string raw = e.WebMessageAsJson;
                if (string.IsNullOrEmpty(raw)) return;

                Dictionary<string, object> msg =
                    json.DeserializeObject(raw) as Dictionary<string, object>;
                if (msg == null) return;

                string type = HttpBridge.GetStr(msg, "type");

                if (type == "http")
                {
                    Dictionary<string, object> reply = await HttpBridge.SendAsync(msg);
                    PostJson(reply);
                }
                else if (type == "notify")
                {
                    // GM_notification -> 托盘气泡通知
                    string title = HttpBridge.GetStr(msg, "title");
                    string text = HttpBridge.GetStr(msg, "text");
                    if (title.Length == 0) title = AppTitle;
                    if (text.Length == 0) text = title;
                    tray.ShowBalloonTip(4000, title, text, ToolTipIcon.Info);
                }
                else if (type == "clipboard")
                {
                    // GM_setClipboard -> 系统剪贴板
                    string text = HttpBridge.GetStr(msg, "text");
                    if (text.Length > 0 && text.Length <= 1024 * 1024)
                    {
                        try { Clipboard.SetText(text); } catch { }
                    }
                }
            }
            catch (Exception ex)
            {
                LogHost("OnWebMessage error: " + ex.Message);
            }
        }

        private static bool IsAllowedOrigin(string src)
        {
            if (string.IsNullOrEmpty(src)) return false;
            foreach (string host in AllowedHosts)
            {
                if (string.IsNullOrEmpty(host)) continue;
                if (src.Equals("https://" + host, StringComparison.OrdinalIgnoreCase) ||
                    src.Equals("http://" + host, StringComparison.OrdinalIgnoreCase) ||
                    src.StartsWith("https://" + host + "/", StringComparison.OrdinalIgnoreCase) ||
                    src.StartsWith("http://" + host + "/", StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }

        private void PostJson(Dictionary<string, object> obj)
        {
            try
            {
                if (core != null)
                {
                    string s = json.Serialize(obj);
                    core.PostWebMessageAsJson(s);
                }
            }
            catch (Exception ex)
            {
                LogHost("PostWebMessageAsJson failed: " + ex.Message);
            }
        }

        /* ─────────────── 日志 ─────────────── */

        private void LogHost(string msg)
        {
            try
            {
                Directory.CreateDirectory(dataDir);
                string file = Path.Combine(dataDir, "host.log");
                FileInfo fi = new FileInfo(file);
                if (fi.Exists && fi.Length > 1024 * 1024) fi.Delete();
                File.AppendAllText(file,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + msg + Environment.NewLine,
                    Encoding.UTF8);
            }
            catch { }
        }
    }
}
