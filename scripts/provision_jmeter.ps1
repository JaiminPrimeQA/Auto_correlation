# Provision Apache JMeter 5.6.3 with SHA-512 checksum verification (Windows).
# Usage: powershell -File scripts/provision_jmeter.ps1 [-Dest .jmeter]
param([string]$Dest = ".jmeter")

$ErrorActionPreference = "Stop"
$Version = "5.6.3"
$Tarball = "apache-jmeter-$Version.tgz"
$Url = "https://archive.apache.org/dist/jmeter/binaries/$Tarball"
$ShaUrl = "$Url.sha512"

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Set-Location $Dest

Write-Host "Downloading $Tarball ..."
Invoke-WebRequest -Uri $Url -OutFile $Tarball
Invoke-WebRequest -Uri $ShaUrl -OutFile "$Tarball.sha512"

Write-Host "Verifying checksum ..."
$expected = ((Get-Content "$Tarball.sha512") -split '\s+')[0].ToLower()
$actual = (Get-FileHash -Algorithm SHA512 $Tarball).Hash.ToLower()
if ($expected -ne $actual) {
    Write-Error "CHECKSUM MISMATCH`nexpected: $expected`nactual:   $actual"
    exit 1
}
Write-Host "Checksum OK."

tar xzf $Tarball
Write-Host "JMeter available at: $(Get-Location)\apache-jmeter-$Version\bin\jmeter.bat"
