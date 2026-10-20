using System;
using System.Threading;
using System.Windows.Forms;

namespace HookApp
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            bool createdNew;
            using (Mutex mutex = new Mutex(true, "__MUTEX_ID__", out createdNew))
            {
                if (!createdNew)
                {
                    MessageBox.Show(
                        "__APP_TITLE__" + " 已在运行中（请查看右下角托盘图标）。",
                        "__APP_TITLE__", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                try
                {
                    Application.Run(new MainForm());
                }
                finally
                {
                    try { mutex.ReleaseMutex(); } catch { }
                }
            }
        }
    }
}
