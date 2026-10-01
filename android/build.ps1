# Build the Max phone app (debug APK) and install it on a USB-connected phone.
# Uses the JDK + Android SDK in C:\max-android (no Android Studio needed).
#   .\android\build.ps1            build + install if a phone is connected
#   .\android\build.ps1 -NoInstall build only
param([switch]$NoInstall)
$ErrorActionPreference = "Stop"
$tools = "C:\max-android"
$env:JAVA_HOME = "$tools\jdk17"
$env:ANDROID_HOME = "$tools\sdk"
$env:Path = "$tools\jdk17\bin;$tools\sdk\platform-tools;$env:Path"

Push-Location $PSScriptRoot
try {
    if (-not (Test-Path local.properties)) { "sdk.dir=C\:/max-android/sdk" | Out-File -Encoding ascii local.properties }
    & .\gradlew.bat assembleDebug --console=plain
    if ($LASTEXITCODE -ne 0) { throw "Gradle build failed" }
    $apk = Resolve-Path "app\build\outputs\apk\debug\app-debug.apk"
    Write-Host "APK: $apk"
    if ($NoInstall) { return }
    $devices = (& adb devices) | Select-Object -Skip 1 | Where-Object { $_ -match "\tdevice$" }
    if (-not $devices) {
        Write-Host "No phone connected (enable USB debugging and plug it in), or copy the APK to the phone and open it."
        return
    }
    & adb install -r $apk
} finally {
    Pop-Location
}
