param(
    [string]$Branch,
    [ValidateSet('MultiAccountDailyTask')]
    [string]$Task
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptDir
$workspaceRoot = Split-Path -Parent $projectRoot
$iconPath = Join-Path $projectRoot "icons\icon.ico"
$cacheDir = Join-Path $projectRoot "cache"
$choiceFile = Join-Path $cacheDir "launcher_choice.txt"

if (-not (Test-Path -LiteralPath $cacheDir)) {
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
}

function Convert-HexToUnicode([string]$HexSequence) {
    $chars = $HexSequence -split ',' | ForEach-Object { [char][convert]::ToInt32($_, 16) }
    return -join $chars
}

$strDevPrefix      = (Convert-HexToUnicode '5F00,53D1,7248,672C') + ' (Dev) - '
$strStablePrefix   = (Convert-HexToUnicode '7A33,5B9A,7248,672C') + ' (Stable) - '
$strIsolatedPrefix = (Convert-HexToUnicode '72EC,7ACB,7248,672C') + ' - '
$strLocalPrefix    = (Convert-HexToUnicode '672C,5730,5206,652F') + ' - '
$strMainDesc       = Convert-HexToUnicode '005B,4E3B,5DE5,4F5C,533A,005D,0020'
$strIsolatedDesc   = Convert-HexToUnicode '005B,72EC,7ACB,5DE5,4F5C,533A,005D,0020'
$strSwitchDesc     = Convert-HexToUnicode '005B,4E3B,5DE5,4F5C,533A,5207,6362,005D,0020'
$strNoBranchErr    = Convert-HexToUnicode '672A,627E,5230,5339,914D,7684,76EE,6807,5206,652F,FF1A'
$strNoPythonErr    = Convert-HexToUnicode '65E0,6CD5,627E,5230,0020,0050,0079,0074,0068,006F,006E,0020,8FD0,884C,65F6,FF1A'
$strNoEntryErr     = Convert-HexToUnicode '65E0,6CD5,627E,5230,542F,52A8,6587,4EF6,FF1A'
$strCheckingOut    = Convert-HexToUnicode '6B63,5728,68C0,51FA,5206,652F,0020'
$strCheckoutFailed = Convert-HexToUnicode '68C0,51FA,5206,652F,5931,8D25,FF1A'
$strSelectFirst    = Convert-HexToUnicode '8BF7,5148,9009,62E9,4E00,4E2A,5206,652F,76EE,6807'

function Get-GitWorktrees {
    param([string]$Root)
    $lines = git -C $Root worktree list --porcelain
    $worktrees = @()
    $current = @{}
    foreach ($line in $lines) {
        if ($line -match '^worktree (.+)$') {
            if ($current.ContainsKey('path')) {
                $worktrees += [PSCustomObject]$current
                $current = @{}
            }
            $current['path'] = $matches[1].Replace('/', '\')
        } elseif ($line -match '^branch refs/heads/(.+)$') {
            $current['branch'] = $matches[1]
        }
    }
    if ($current.ContainsKey('path')) {
        $worktrees += [PSCustomObject]$current
    }
    return $worktrees
}

function Get-GitBranches {
    param([string]$Root)
    $branches = git -C $Root branch --list --format="%(refname:short)"
    return $branches
}

function Launch-Target {
    param(
        [string]$TargetWorkspaceRoot,
        [string]$TaskName
    )
    $targetProject = Join-Path $TargetWorkspaceRoot 'ok-wuthering-waves'
    $targetPythonw = Join-Path $TargetWorkspaceRoot '.venv\Scripts\pythonw.exe'
    $targetEntry = Join-Path $targetProject 'main.py'

    if (-not (Test-Path -LiteralPath $targetPythonw -PathType Leaf)) {
        throw "$strNoPythonErr $targetPythonw"
    }
    if (-not (Test-Path -LiteralPath $targetEntry -PathType Leaf)) {
        throw "$strNoEntryErr $targetEntry"
    }

    $launchArgs = @('"' + $targetEntry + '"')
    if ($TaskName) {
        $launchArgs += @('-t', $TaskName)
    }

    Start-Process `
        -FilePath $targetPythonw `
        -ArgumentList $launchArgs `
        -WorkingDirectory $targetProject `
        -Verb RunAs `
        -WindowStyle Hidden
}

$worktrees = Get-GitWorktrees -Root $workspaceRoot
$allBranches = Get-GitBranches -Root $workspaceRoot

$worktreeBranches = @{}
$items = @()

foreach ($wt in $worktrees) {
    $branchName = $wt.branch
    $worktreeBranches[$branchName] = $wt.path

    if ($wt.path.TrimEnd('\') -ieq $workspaceRoot.TrimEnd('\')) {
        $label = $strDevPrefix + $branchName
        $desc = $strMainDesc + $wt.path
    } elseif ($branchName -ieq "stable") {
        $label = $strStablePrefix + $branchName
        $desc = $strIsolatedDesc + $wt.path
    } else {
        $label = $strIsolatedPrefix + $branchName
        $desc = $strIsolatedDesc + $wt.path
    }

    $items += [PSCustomObject]@{
        DisplayName = $label
        Description = $desc
        Branch      = $branchName
        Path        = $wt.path
        IsWorktree  = $true
    }
}

foreach ($b in $allBranches) {
    if (-not $worktreeBranches.ContainsKey($b)) {
        $items += [PSCustomObject]@{
            DisplayName = $strLocalPrefix + $b
            Description = $strSwitchDesc + $workspaceRoot
            Branch      = $b
            Path        = $workspaceRoot
            IsWorktree  = $false
        }
    }
}

if ($Branch) {
    $matched = $items | Where-Object { $_.Branch -ieq $Branch } | Select-Object -First 1
    if (-not $matched) {
        throw "$strNoBranchErr $Branch"
    }
    if (-not $matched.IsWorktree) {
        $checkoutOutput = git -C $workspaceRoot checkout $matched.Branch 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "$strCheckoutFailed $checkoutOutput"
        }
    }
    Set-Content -LiteralPath $choiceFile -Value $matched.Branch -Encoding UTF8
    Launch-Target -TargetWorkspaceRoot $matched.Path -TaskName $Task
    exit 0
}

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

[xml]$xaml = @"
<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="OK-WW &#x5206;&#x652F;&#x542F;&#x52A8;&#x9009;&#x62E9;&#x5668;"
    Height="510"
    Width="500"
    WindowStartupLocation="CenterScreen"
    ResizeMode="NoResize"
    Background="#F8F9FA"
    FontFamily="Segoe UI, Microsoft YaHei UI">
    <Window.Resources>
        <Style TargetType="ListBoxItem">
            <Setter Property="Padding" Value="10,8"/>
            <Setter Property="Margin" Value="0,3"/>
            <Setter Property="Background" Value="#FFFFFF"/>
            <Setter Property="BorderBrush" Value="#E1E4E8"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Cursor" Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="ListBoxItem">
                        <Border x:Name="Bd"
                                Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="6"
                                Padding="{TemplateBinding Padding}"
                                SnapsToDevicePixels="true">
                            <ContentPresenter HorizontalAlignment="Stretch" VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="true">
                                <Setter TargetName="Bd" Property="Background" Value="#F1F5F9"/>
                                <Setter TargetName="Bd" Property="BorderBrush" Value="#CBD5E1"/>
                            </Trigger>
                            <Trigger Property="IsSelected" Value="true">
                                <Setter TargetName="Bd" Property="Background" Value="#E0F2FE"/>
                                <Setter TargetName="Bd" Property="BorderBrush" Value="#0284C7"/>
                                <Setter TargetName="Bd" Property="BorderThickness" Value="2"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>
    </Window.Resources>

    <Grid Margin="18">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,12">
            <TextBlock Text="OK-WW &#x7EDF;&#x4E00;&#x542F;&#x52A8;&#x5668;" FontSize="18" FontWeight="Bold" Foreground="#0F172A"/>
            <TextBlock Text="&#x8BF7;&#x9009;&#x62E9;&#x542F;&#x52A8;&#x7684;&#x76EE;&#x6807;&#x5206;&#x652F;&#x73AF;&#x5883;&#xFF0C;&#x53CC;&#x51FB;&#x6216;&#x70B9;&#x51FB;&#x4E0B;&#x65B9;&#x542F;&#x52A8;&#x8FDB;&#x5165;" FontSize="12" Foreground="#64748B" Margin="0,4,0,0"/>
        </StackPanel>

        <ListBox x:Name="BranchListBox" Grid.Row="1" Background="Transparent" BorderThickness="0" ScrollViewer.HorizontalScrollBarVisibility="Disabled">
            <ListBox.ItemTemplate>
                <DataTemplate>
                    <StackPanel>
                        <TextBlock Text="{Binding DisplayName}" FontSize="14" FontWeight="SemiBold" Foreground="#1E293B"/>
                        <TextBlock Text="{Binding Description}" FontSize="11" Foreground="#64748B" Margin="0,3,0,0"/>
                    </StackPanel>
                </DataTemplate>
            </ListBox.ItemTemplate>
        </ListBox>

        <CheckBox x:Name="DailyTaskCheckBox" Grid.Row="2" Content="&#x4EE5;&#x591A;&#x8D26;&#x6237;&#x65E5;&#x5E38;&#x6A21;&#x5F0F;&#x542F;&#x52A8; (-t MultiAccountDailyTask)" Margin="2,10,0,0" FontSize="12" Foreground="#475569" Cursor="Hand"/>

        <TextBlock x:Name="StatusMessage" Grid.Row="3" Text="" FontSize="12" Foreground="#DC2626" Margin="0,6,0,0" TextWrapping="Wrap"/>

        <Grid Grid.Row="4" Margin="0,12,0,0">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>

            <Button x:Name="CancelButton" Grid.Column="1" Content="&#x53D6;&#x6D88;" Width="85" Height="34" Margin="0,0,8,0"
                    Background="#FFFFFF" BorderBrush="#CBD5E1" BorderThickness="1" Foreground="#334155" FontSize="13" Cursor="Hand" IsCancel="True">
                <Button.Resources>
                    <Style TargetType="Border">
                        <Setter Property="CornerRadius" Value="6"/>
                    </Style>
                </Button.Resources>
            </Button>

            <Button x:Name="LaunchButton" Grid.Column="2" Content="&#x542F;&#x52A8; OK-WW" Width="110" Height="34"
                    Background="#0284C7" BorderBrush="#0284C7" BorderThickness="1" Foreground="#FFFFFF" FontWeight="SemiBold" FontSize="13" Cursor="Hand" IsDefault="True">
                <Button.Resources>
                    <Style TargetType="Border">
                        <Setter Property="CornerRadius" Value="6"/>
                    </Style>
                </Button.Resources>
            </Button>
        </Grid>
    </Grid>
</Window>
"@

$reader = [System.Xml.XmlNodeReader]::new($xaml)
$window = [System.Windows.Markup.XamlReader]::Load($reader)

if (Test-Path -LiteralPath $iconPath) {
    $window.Icon = [System.Windows.Media.Imaging.BitmapFrame]::Create([System.Uri]::new($iconPath))
}

$listBox = $window.FindName("BranchListBox")
$dailyTaskCheckBox = $window.FindName("DailyTaskCheckBox")
$launchButton = $window.FindName("LaunchButton")
$cancelButton = $window.FindName("CancelButton")
$statusText = $window.FindName("StatusMessage")

$listBox.ItemsSource = $items

$lastChoice = ""
if (Test-Path -LiteralPath $choiceFile) {
    $lastChoice = (Get-Content -LiteralPath $choiceFile -Raw -ErrorAction SilentlyContinue)
    if ($lastChoice) {
        $lastChoice = $lastChoice.Trim()
    }
}

$selectedItem = $items | Where-Object { $_.Branch -ieq $lastChoice } | Select-Object -First 1
if (-not $selectedItem) {
    $selectedItem = $items | Select-Object -First 1
}
$listBox.SelectedItem = $selectedItem

$doLaunch = {
    $chosen = $listBox.SelectedItem
    if (-not $chosen) {
        $statusText.Text = $strSelectFirst
        return
    }

    if (-not $chosen.IsWorktree) {
        $statusText.Text = "$strCheckingOut$($chosen.Branch)..."
        $checkoutOutput = git -C $workspaceRoot checkout $chosen.Branch 2>&1
        if ($LASTEXITCODE -ne 0) {
            $statusText.Text = "$strCheckoutFailed $checkoutOutput"
            return
        }
    }

    Set-Content -LiteralPath $choiceFile -Value $chosen.Branch -Encoding UTF8
    $window.Close()

    $taskParam = $null
    if ($dailyTaskCheckBox.IsChecked) {
        $taskParam = 'MultiAccountDailyTask'
    }

    Launch-Target -TargetWorkspaceRoot $chosen.Path -TaskName $taskParam
}

$launchButton.Add_Click({
    & $doLaunch
})

$listBox.Add_MouseDoubleClick({
    & $doLaunch
})

$cancelButton.Add_Click({
    $window.Close()
})

$window.ShowDialog() | Out-Null
