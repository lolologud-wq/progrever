$ErrorActionPreference = "Stop"

$HostName = "185.199.196.104"
$User = "root"
$Password = ConvertTo-SecureString "SRqVImGA3kDaZ" -AsPlainText -Force
$Credential = New-Object System.Management.Automation.PSCredential($User, $Password)
$RemoteDir = "/opt/progrever"
$ZipPath = "C:\Users\USER\Desktop\progrever-deploy.zip"

Import-Module Posh-SSH

Write-Host "Connecting to $HostName..."
$Ssh = New-SSHSession -ComputerName $HostName -Credential $Credential -AcceptKey -Force

function Invoke-Remote($cmd) {
    Write-Host "`$ $cmd"
    $r = Invoke-SSHCommand -SessionId $Ssh.SessionId -Command $cmd -TimeOut 600
    if ($r.Output) { $r.Output | ForEach-Object { Write-Host $_ } }
    if ($r.Error) { $r.Error | ForEach-Object { Write-Host $_ -ForegroundColor Red } }
    if ($r.ExitStatus -ne 0) { throw "Command failed ($($r.ExitStatus)): $cmd" }
}

Write-Host "Uploading archive..."
Set-SCPItem -ComputerName $HostName -Credential $Credential -Path $ZipPath -Destination "/tmp/" -AcceptKey -Force

Invoke-Remote "apt-get update -qq && apt-get install -y -qq unzip python3 python3-pip python3-venv"
Invoke-Remote "mkdir -p $RemoteDir && rm -rf $RemoteDir/* && unzip -o /tmp/progrever-deploy.zip -d $RemoteDir"
Invoke-Remote "mkdir -p $RemoteDir/sessions $RemoteDir/media"
Invoke-Remote "chmod +x $RemoteDir/deploy/setup_server.sh"
Invoke-Remote "bash $RemoteDir/deploy/setup_server.sh"

Remove-SSHSession -SessionId $Ssh.SessionId | Out-Null
Write-Host "`nDeploy complete! Bot: $HostName" -ForegroundColor Green
