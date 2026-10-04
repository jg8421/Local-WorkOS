param([Parameter(Mandatory=$true)][string]$WorkbookPath,[Parameter(Mandatory=$true)][string]$ResultPath)
$ErrorActionPreference='Stop'
$excel=$null;$book=$null;$sheet=$null
try {
 $excel=New-Object -ComObject Excel.Application
 $excel.Visible=$false;$excel.DisplayAlerts=$false;$excel.AskToUpdateLinks=$false;$excel.AutomationSecurity=3
 $book=$excel.Workbooks.Open($WorkbookPath,0,$false)
 $excel.CalculateFullRebuild()
 $sheet=$book.Worksheets.Item('Returns')
 $result=@{moic=$sheet.Range('B4').Value2;irr=$sheet.Range('B5').Value2;total_invested=$sheet.Range('B6').Value2;total_received=$sheet.Range('B7').Value2;exit_proceeds=$sheet.Range('E14').Value2;entry_ownership=$sheet.Range('E10').Value2;exit_ownership=$sheet.Range('E12').Value2;exit_equity_value=$sheet.Range('E13').Value2}
 foreach($key in @('moic','total_invested','total_received')){if($result[$key] -isnot [double] -and $result[$key] -isnot [int]){throw ('Excel formula failed: '+$key)}}
 if($sheet.Range('B5').HasFormula -and $result.irr -isnot [double]){throw 'Excel XIRR did not converge'}
 $book.Save()
 $result | ConvertTo-Json -Compress | Set-Content -LiteralPath $ResultPath -Encoding UTF8
} finally {
 if($book){$book.Close($false)}
 if($sheet){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet)}
 if($book){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($book)}
 if($excel){$excel.Quit();[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)}
}
