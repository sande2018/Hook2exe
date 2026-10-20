using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;

namespace HookApp
{
    /// <summary>
    /// 页面脚本 -> 宿主 exe 的通用 HTTP 转发桥。
    /// 页面里的 GM 兼容层通过 window.chrome.webview.postMessage 发来
    /// { type:"http", id, method, url, headers, body }，
    /// 本类用 HttpClient 转发（无 CORS 限制），并生成回推字典
    /// { type:"http-resp", id, status, body / error }。
    /// </summary>
    internal static class HttpBridge
    {
        private static readonly HttpClient http = CreateClient();

        private static HttpClient CreateClient()
        {
            HttpClient client = new HttpClient();
            client.Timeout = TimeSpan.FromSeconds(70);
            client.DefaultRequestHeaders.Add("User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HookApp/1.0");
            return client;
        }

        public static string GetStr(Dictionary<string, object> msg, string key)
        {
            object v;
            if (msg != null && msg.TryGetValue(key, out v) && v != null) return v.ToString();
            return "";
        }

        public static Dictionary<string, object> GetDict(Dictionary<string, object> msg, string key)
        {
            object v;
            if (msg != null && msg.TryGetValue(key, out v)) return v as Dictionary<string, object>;
            return null;
        }

        /// <summary>处理一条 http 桥消息，返回应回推给页面的结果字典。</summary>
        public static async Task<Dictionary<string, object>> SendAsync(Dictionary<string, object> msg)
        {
            Dictionary<string, object> reply = new Dictionary<string, object>();
            reply["type"] = "http-resp";
            reply["id"] = GetStr(msg, "id");

            string method = GetStr(msg, "method");
            string url = GetStr(msg, "url");
            string body = null;
            if (msg != null && msg.ContainsKey("body") && msg["body"] != null)
                body = msg["body"].ToString();
            Dictionary<string, object> headers = GetDict(msg, "headers");

            try
            {
                if (!url.StartsWith("http://", StringComparison.OrdinalIgnoreCase) &&
                    !url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                    throw new ArgumentException("only http/https urls are allowed");

                Uri uri = new Uri(url);
                if (IsPrivateHost(uri.Host))
                    throw new ArgumentException("private/loopback hosts are not allowed: " + uri.Host);

                HttpMethod m = new HttpMethod(
                    string.IsNullOrEmpty(method) ? "GET" : method.ToUpperInvariant());

                HttpRequestMessage req = new HttpRequestMessage(m, uri);

                string contentType = "application/json";
                if (headers != null)
                {
                    foreach (KeyValuePair<string, object> kv in headers)
                    {
                        if (kv.Value == null) continue;
                        string name = kv.Key;
                        string value = kv.Value.ToString();
                        if (string.Equals(name, "Content-Type", StringComparison.OrdinalIgnoreCase))
                        {
                            contentType = value;
                            continue;
                        }
                        req.Headers.TryAddWithoutValidation(name, value);
                    }
                }

                if (body != null)
                    req.Content = new StringContent(body, Encoding.UTF8, contentType);

                using (HttpResponseMessage resp = await http.SendAsync(req))
                {
                    int status = (int)resp.StatusCode;
                    string respBody = await resp.Content.ReadAsStringAsync();
                    reply["status"] = status;
                    reply["body"] = respBody;
                }
            }
            catch (Exception ex)
            {
                reply["status"] = 0;
                reply["error"] = ex.Message;
            }

            return reply;
        }

        /// <summary>SSRF 基础防护：拒绝回环 / 内网 / 链路本地地址。</summary>
        private static bool IsPrivateHost(string host)
        {
            if (string.IsNullOrEmpty(host)) return true;
            string h = host.ToLowerInvariant();
            if (h == "localhost" || h == "::1" || h == "127.0.0.1") return true;
            if (h.StartsWith("127.") || h.StartsWith("192.168.") ||
                h.StartsWith("10.") || h.StartsWith("169.254."))
                return true;
            if (h.StartsWith("172."))
            {
                string[] parts = h.Split('.');
                int second;
                if (parts.Length == 4 && int.TryParse(parts[1], out second))
                    return second >= 16 && second <= 31;
            }
            return false;
        }
    }
}
