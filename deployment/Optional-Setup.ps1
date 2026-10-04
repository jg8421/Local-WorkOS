$ErrorActionPreference='Stop'
Write-Host 'WorkOS可选环境。这里只打开官方页面，不安装软件、不修改注册表或登录账号。'
Write-Host '1: Python官方安装说明（便携包已内置，通常无需安装）'
Write-Host '2: Node官方页面（仅DSH等可选扩展需要）'
Write-Host '3: Microsoft Excel产品页（原生投资回报需要本人授权的Excel）'
Write-Host 'Enter: 退出'
$choice=Read-Host '明确选择要打开的页面'
$url=switch($choice){'1'{'https://www.python.org/downloads/windows/'} '2'{'https://nodejs.org/en/download'} '3'{'https://www.microsoft.com/microsoft-365/excel'} default{''}}
if($url){Start-Process $url -WindowStyle Hidden}
