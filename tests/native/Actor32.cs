using System;
using System.Diagnostics;
using System.IO;
using System.Threading;

class Actor32 {
    static int Main(string[] args) {
        File.WriteAllText(args[3], Environment.Is64BitProcess ? "64" : "32");
        for (int i = 0; i < 1200; i++) {
            if (File.Exists(args[1])) {
                File.Delete(args[1]);
                var info = new ProcessStartInfo(args[0], "--child \"" + args[2] + "\" {#notfullscreen#:1,#fullscreen#:1}");
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                using (var child = Process.Start(info)) { child.WaitForExit(); }
            }
            Thread.Sleep(50);
        }
        return 0;
    }
}
