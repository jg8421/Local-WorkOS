param([Parameter(Mandatory=$true)][string]$WorkbookPath,[Parameter(Mandatory=$true)][string]$ResultPath)
# The Python test creates and owns this synthetic temporary workbook.
$ErrorActionPreference='Stop';$excel=$null;$book=$null;$sheet=$null
try {
 $excel=New-Object -ComObject Excel.Application
 $excel.Visible=$false;$excel.DisplayAlerts=$false;$excel.AutomationSecurity=3
 $book=$excel.Workbooks.Open($WorkbookPath,0,$false);$sheet=$book.Worksheets.Item('Returns')
 $sheet.Range('B15').Value2=240.0
 $sheet.Range('B13').Value2=0.1
 $sheet.Range('B21').Formula='=DATE(2032,12,31)'
 $excel.CalculateFullRebuild()
 @{moic=$sheet.Range('B4').Value2;irr=$sheet.Range('B5').Value2;received=$sheet.Range('B7').Value2} | ConvertTo-Json -Compress | Set-Content -LiteralPath $ResultPath -Encoding UTF8
 $book.Save()
} finally {
 if($book){$book.Close($false)}
 if($sheet){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet)}
 if($book){[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($book)}
 if($excel){$excel.Quit();[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)}
}
