$patterns = @(
  '*manage.py runserver 127.0.0.1:8000*',
  '*npm.cmd start -- --host 127.0.0.1 --port 4200*',
  '*python.exe" -m fabric_agent*',
  '*python.exe -m fabric_agent*'
)

$targets = Get-CimInstance Win32_Process | Where-Object {
  $cmd = $_.CommandLine
  if (-not $cmd) {
    return $false
  }

  foreach ($pattern in $patterns) {
    if ($cmd -like $pattern) {
      return $true
    }
  }

  return $false
}

$portPids = @(
  Get-NetTCPConnection -LocalPort 8000,4200 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
)

$allPids = @($targets.ProcessId + $portPids | Where-Object { $_ } | Select-Object -Unique)

if (-not $allPids -or $allPids.Count -eq 0) {
  Write-Host 'No Fabric dev processes found.'
  exit 0
}

foreach ($pidValue in $allPids) {
  try {
    Stop-Process -Id $pidValue -Force -ErrorAction Stop
    Write-Host ("Stopped PID $pidValue")
  } catch {
    Write-Host ("Could not stop PID ${pidValue}: " + $_.Exception.Message)
  }
}
