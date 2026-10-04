<#
.SYNOPSIS
    LearnUs 智能助学工作区与出勤助手 (Windows 原生桌面客户端)
    集成: 自动登录 + 知识库一键同步 + 2倍速自动出勤浏览 + 阿里 SenseVoice 讲义提取 + doc转md
#>

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Drawing, System.Windows.Forms

$csharpCode = @"
using System;
using System.Diagnostics;
using System.Collections.Concurrent;
using System.Runtime.InteropServices;

namespace LearnUsClient {
    public static class NativeConsoleHelper {
        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("kernel32.dll")]
        public static extern IntPtr GetConsoleWindow();
    }

    public class TaskRunner {
        private Process _process;
        public ConcurrentQueue<string> OutputQueue = new ConcurrentQueue<string>();
        public volatile bool IsRunning = false;
        public volatile bool IsCompleted = false;
        public volatile int ExitCode = -1;

        public void Start(string exe, string arguments) {
            IsRunning = true;
            IsCompleted = false;
            ExitCode = -1;

            try {
                var psi = new ProcessStartInfo {
                    FileName = exe,
                    Arguments = arguments,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                    StandardOutputEncoding = System.Text.Encoding.UTF8,
                    StandardErrorEncoding = System.Text.Encoding.UTF8
                };
                _process = new Process { StartInfo = psi, EnableRaisingEvents = true };
                _process.OutputDataReceived += (s, e) => {
                    if (e.Data != null) OutputQueue.Enqueue(e.Data);
                };
                _process.ErrorDataReceived += (s, e) => {
                    if (e.Data != null) OutputQueue.Enqueue(e.Data);
                };
                _process.Exited += (s, e) => {
                    try { ExitCode = _process.ExitCode; } catch { ExitCode = -1; }
                    IsRunning = false;
                    IsCompleted = true;
                };

                _process.Start();
                _process.BeginOutputReadLine();
                _process.BeginErrorReadLine();
            } catch (Exception ex) {
                OutputQueue.Enqueue("[!] 启动异常: " + ex.Message);
                IsRunning = false;
                IsCompleted = true;
            }
        }

        public void Kill() {
            try {
                if (_process != null && !_process.HasExited) {
                    _process.Kill();
                }
            } catch {}
        }
    }
}
"@

try {
    Add-Type -TypeDefinition $csharpCode -ErrorAction SilentlyContinue
} catch {}

$script:runner = New-Object LearnUsClient.TaskRunner
$script:pollTimer = $null
$script:projDir = if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) { $PSScriptRoot } else { "G:\project\learnus-helper" }
$script:workspaceDir = Join-Path $script:projDir "workspace"

# ----------------- XAML 现代化暗色界面 -----------------
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="LearnUs 智能助学知识库与自动出勤工作台" Height="680" Width="920"
        WindowStartupLocation="CenterScreen" Background="#14171F" FontFamily="Segoe UI, Microsoft YaHei"
        MinHeight="580" MinWidth="800">
    <Window.Resources>
        <Style TargetType="Button">
            <Setter Property="Background" Value="#2563EB"/>
            <Setter Property="Foreground" Value="#FFFFFF"/>
            <Setter Property="FontSize" Value="13"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Padding" Value="14,8"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border Background="{TemplateBinding Background}" CornerRadius="6" Padding="{TemplateBinding Padding}">
                            <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
                        </Border>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </Window.Resources>

    <Grid Margin="20">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <!-- 顶部标题与状态指示 -->
        <Border Grid.Row="0" Background="#1E232E" CornerRadius="8" Padding="16,12" Margin="0,0,0,16">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <StackPanel Orientation="Vertical">
                    <TextBlock Text="🎓 延世大学 LearnUs 智能助学知识库与自动出勤助手" FontSize="18" FontWeight="Bold" Foreground="#F3F4F6"/>
                    <TextBlock Text="像学霸一样自动下载、doc转md、SenseVoice外语讲义提取、2.0倍速安全无头挂机出勤" FontSize="12" Foreground="#9CA3AF" Margin="0,4,0,0"/>
                </StackPanel>
                <StackPanel Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center">
                    <Ellipse x:Name="IndicatorDot" Width="10" Height="10" Fill="#10B981" Margin="0,0,8,0"/>
                    <TextBlock x:Name="StatusText" Text="就绪 (已加载会话 Cookie)" Foreground="#D1D5DB" FontSize="12" VerticalAlignment="Center"/>
                </StackPanel>
            </Grid>
        </Border>

        <!-- 操作与参数控制面板 -->
        <Border Grid.Row="1" Background="#1E232E" CornerRadius="8" Padding="16" Margin="0,0,0,16">
            <StackPanel Orientation="Vertical">
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="Auto"/>
                    </Grid.ColumnDefinitions>
                    <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
                        <TextBlock Text="指定课程 (留空为全课):" Foreground="#9CA3AF" FontSize="13" VerticalAlignment="Center" Margin="0,0,8,0"/>
                        <TextBox x:Name="TxtCourse" Width="200" Height="30" Background="#0F172A" Foreground="#FFFFFF" BorderBrush="#334155" BorderThickness="1" Padding="6,4" VerticalContentAlignment="Center"/>
                    </StackPanel>
                    <StackPanel Grid.Column="1" Orientation="Horizontal">
                        <Button x:Name="BtnTodo" Content="📋 查看未交作业与待办" Background="#374151" Margin="0,0,8,0"/>
                        <Button x:Name="BtnOpenFolder" Content="📂 打开知识库文件夹" Background="#059669"/>
                    </StackPanel>
                </Grid>

                <Separator Background="#334155" Margin="0,12"/>

                <!-- 两大核心动作按钮 -->
                <Grid>
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="16"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>

                    <!-- 按钮 1: 学霸全自动同步 -->
                    <Border Grid.Column="0" Background="#132338" BorderBrush="#1E40AF" BorderThickness="1" CornerRadius="6" Padding="12">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>
                            <StackPanel>
                                <TextBlock Text="📚 知识库全自动同步 (Sync)" FontWeight="Bold" Foreground="#60A5FA" FontSize="14"/>
                                <TextBlock Text="下载课件+视频、doc转md、SenseVoice讲义提取、生成README树" Foreground="#94A3B8" FontSize="11" Margin="0,4,0,0" TextWrapping="Wrap"/>
                            </StackPanel>
                            <Button x:Name="BtnSync" Grid.Column="1" Content="▶ 开始同步" Background="#2563EB" VerticalAlignment="Center" Margin="10,0,0,0"/>
                        </Grid>
                    </Border>

                    <!-- 按钮 2: 自动出勤挂机 -->
                    <Border Grid.Column="2" Background="#1E1E38" BorderBrush="#6D28D9" BorderThickness="1" CornerRadius="6" Padding="12">
                        <Grid>
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>
                            <StackPanel>
                                <TextBlock Text="⚡ 自动浏览与出勤挂机 (Watch 2.0x)" FontWeight="Bold" Foreground="#A78BFA" FontSize="14"/>
                                <TextBlock Text="无头无声后台模拟 2.0 倍速真实心跳上报，自动按顺序看完未达标视频" Foreground="#94A3B8" FontSize="11" Margin="0,4,0,0" TextWrapping="Wrap"/>
                            </StackPanel>
                            <Button x:Name="BtnWatch" Grid.Column="1" Content="🚀 开始挂机" Background="#7C3AED" VerticalAlignment="Center" Margin="10,0,0,0"/>
                        </Grid>
                    </Border>
                </Grid>
            </StackPanel>
        </Border>

        <!-- 终端控制台实时日志输出 -->
        <Border Grid.Row="2" Background="#0A0D14" BorderBrush="#262D3D" BorderThickness="1" CornerRadius="8" Padding="12">
            <Grid>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                </Grid.RowDefinitions>
                <TextBlock Grid.Row="0" Text="实时运行日志 (Terminal Output)" FontSize="12" Foreground="#6B7280" Margin="0,0,0,8"/>
                <TextBox x:Name="LogBox" Grid.Row="1" Background="Transparent" Foreground="#E2E8F0"
                         FontFamily="Consolas, Cascadia Code, Courier New" FontSize="12"
                         BorderThickness="0" IsReadOnly="True" VerticalScrollBarVisibility="Auto"
                         HorizontalScrollBarVisibility="Auto" TextWrapping="Wrap"/>
            </Grid>
        </Border>

        <!-- 底部终止控制与状态 -->
        <Grid Grid.Row="3" Margin="0,12,0,0">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock x:Name="BottomStatus" Text="状态: 空闲中，随时可以点击上方按钮执行任务" Foreground="#9CA3AF" FontSize="12" VerticalAlignment="Center"/>
            <Button x:Name="BtnStop" Grid.Column="1" Content="⏹ 终止当前任务" Background="#DC2626" Visibility="Collapsed"/>
        </Grid>
    </Grid>
</Window>
"@

$reader = (New-Object System.Xml.XmlNodeReader $xaml)
$window = [System.Windows.Markup.XamlReader]::Load($reader)

# 绑定控件
$txtCourse = $window.FindName("TxtCourse")
$btnTodo = $window.FindName("BtnTodo")
$btnOpenFolder = $window.FindName("BtnOpenFolder")
$btnSync = $window.FindName("BtnSync")
$btnWatch = $window.FindName("BtnWatch")
$btnStop = $window.FindName("BtnStop")
$logBox = $window.FindName("LogBox")
$indicatorDot = $window.FindName("IndicatorDot")
$statusText = $window.FindName("StatusText")
$bottomStatus = $window.FindName("BottomStatus")

function Append-Log([string]$msg) {
    if ([string]::IsNullOrEmpty($msg)) { return }
    $window.Dispatcher.Invoke([Action]{
        $logBox.AppendText($msg + "`r`n")
        $logBox.ScrollToEnd()
    })
}

function Start-WslCommand([string]$learnusCmd) {
    $script:runner.Kill()
    $logBox.Clear()

    $btnSync.IsEnabled = $false
    $btnWatch.IsEnabled = $false
    $btnTodo.IsEnabled = $false
    $btnStop.Visibility = "Visible"
    $indicatorDot.Fill = [System.Windows.Media.Brushes]::Orange
    $statusText.Text = "正在执行任务..."
    $bottomStatus.Text = "正在后台通过 WSL2 驱动 Python & Playwright 环境..."

    $wslArgs = "-d Ubuntu-Agent -u miner --cd /mnt/g/project/learnus-helper /home/miner/subtitle_pipeline/.venv/bin/python learnus.py $learnusCmd"
    Append-Log "[*] 执行指令: wsl.exe $wslArgs`r`n"

    $script:runner.Start("wsl.exe", $wslArgs)

    $script:pollTimer = New-Object System.Windows.Threading.DispatcherTimer
    $script:pollTimer.Interval = [TimeSpan]::FromMilliseconds(150)
    $script:pollTimer.Add_Tick({
        $line = $null
        while ($script:runner.OutputQueue.TryDequeue([ref]$line)) {
            Append-Log $line
        }

        if ($script:runner.IsCompleted) {
            $script:pollTimer.Stop()
            $btnSync.IsEnabled = $true
            $btnWatch.IsEnabled = $true
            $btnTodo.IsEnabled = $true
            $btnStop.Visibility = "Collapsed"
            $indicatorDot.Fill = [System.Windows.Media.Brushes]::LimeGreen
            $statusText.Text = "就绪"
            $bottomStatus.Text = "任务完成！退出代码: $($script:runner.ExitCode)"
            Append-Log "`r`n[+] 任务完成 (退出码: $($script:runner.ExitCode))`r`n"
        }
    })
    $script:pollTimer.Start()
}

# 按钮点击事件
$btnSync.Add_Click({
    $course = $txtCourse.Text.Trim()
    $cmd = "sync"
    if (-not [string]::IsNullOrWhiteSpace($course)) {
        $cmd += " --course `"$course`""
    }
    Start-WslCommand $cmd
})

$btnWatch.Add_Click({
    $course = $txtCourse.Text.Trim()
    $cmd = "watch --speed 2.0"
    if (-not [string]::IsNullOrWhiteSpace($course)) {
        $cmd += " --course `"$course`""
    }
    Start-WslCommand $cmd
})

$btnTodo.Add_Click({
    $course = $txtCourse.Text.Trim()
    $cmd = "todo"
    if (-not [string]::IsNullOrWhiteSpace($course)) {
        $cmd += " --course `"$course`""
    }
    Start-WslCommand $cmd
})

$btnOpenFolder.Add_Click({
    if (Test-Path $script:workspaceDir) {
        Invoke-Item $script:workspaceDir
    } else {
        New-Item -ItemType Directory -Path $script:workspaceDir -Force | Out-Null
        Invoke-Item $script:workspaceDir
    }
})

$btnStop.Add_Click({
    $script:runner.Kill()
    # 联动强杀 WSL 内相关任务
    Start-Process "wsl.exe" -ArgumentList "-d Ubuntu-Agent -u miner pkill -9 -f learnus.py" -NoNewWindow -Wait
    Append-Log "`r`n[!] 用户手动终止了当前任务。`r`n"
})

$window.Add_Closing({
    if ($script:pollTimer) { $script:pollTimer.Stop() }
    $script:runner.Kill()
    Start-Process "wsl.exe" -ArgumentList "-d Ubuntu-Agent -u miner pkill -9 -f learnus.py" -NoNewWindow -Wait
})

# 启动窗口
[LearnUsClient.NativeConsoleHelper]::ShowWindow([LearnUsClient.NativeConsoleHelper]::GetConsoleWindow(), 0)
$window.ShowDialog() | Out-Null
