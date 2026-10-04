// Public artifact-tool API authors the workbook; Microsoft Excel recalculates it.
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const [input, output, modules, preview] = process.argv.slice(2);
const require = createRequire(import.meta.url);
const {Workbook, SpreadsheetFile} = await import(pathToFileURL(require.resolve('@oai/artifact-tool', {paths:[modules]})).href);
const {assumptions:a, result:r} = JSON.parse(await fs.readFile(input, 'utf8'));
const wb = Workbook.create(), s = wb.worksheets.add('Returns');
s.showGridLines = false;
const value = (cell, v) => s.getRange(cell).values = [[v ?? '']];
const formula = (cell, v) => {s.getRange(cell).formulas = [[v]]; s.getRange(cell).format.font.color = '#000000';};
const text = v => String(v ?? '').replace(/^[=+@-]/, x => "'" + x);
value('A1','Investor Returns'); value('A2',text(r.currency + ' · ' + r.unit + ' · gross investor cash flows'));
value('A4','MOC / MOIC'); value('A5','IRR (XIRR)'); value('A6','Total invested'); value('A7','Total received');
value('A9','Inputs — blue cells are editable');
const inputs = [['Entry equity value',a.entry_equity_value],['Initial investment',a.investment_amount],
 ['Entry ownership (if supplied)',a.entry_ownership],['IPO dilution',a.ipo_dilution ?? 0],
 ['Final ownership (if supplied)',a.exit_ownership],['Exit net income',a.exit_net_income],
 ['Exit P/E',a.exit_pe_multiple],['Exit equity value (if supplied)',a.exit_equity_value],
 ['Exit proceeds (if supplied)',a.exit_proceeds],['Entry valuation basis',a.entry_valuation_basis],
 ['Entry date',r.entry_date],['Exit date',r.exit_date]];
inputs.forEach(([label,v],i)=>{value('A'+(10+i),label);value('B'+(10+i),typeof v==='string'?text(v):v);});
for (const [cell, day] of [['B20',r.entry_date],['B21',r.exit_date]]) {
 const [y,m,d] = day.split('-').map(Number); formula(cell,`=DATE(${y},${m},${d})`);
 s.getRange(cell).setNumberFormat('yyyy-mm-dd');
}
value('D10','Entry ownership');
formula('E10','=IF(ISNUMBER(B12),B12,IF(ISNUMBER(B10),B11/IF(B19="pre_money",B10+B11,B10),0))');
value('D11','Dilution events (excluding IPO input)');
(a.dilution_events || []).forEach((n,i)=>value('G'+(10+i),n));
value('D12','Exit ownership');
formula('E12',`=IF(ISNUMBER(B14),B14,E10*(1-B13)*PRODUCT(${Array.from({length:20},(_,i)=>'1-G'+(10+i)).join(',')}))`);
value('D13','Exit company equity'); formula('E13','=IF(ISNUMBER(B17),B17,B15*B16)');
value('D14','Investor exit proceeds'); formula('E14','=IF(ISNUMBER(B18),B18,E13*E12)');
value('A24','Date');value('B24','Investment');value('C24','Distribution');value('D24','Net cash flow');value('E24','Event');
const full = a.cash_flows != null;
if(full)for(const cell of ['E10','E12','E13','E14'])value(cell,'Cash-flow schedule below');
// Base rows are tied to economic inputs; edits change cash flows and returns.
const flows = full ? r.cash_flows : [{date:r.entry_date,amount:-a.investment_amount,label:'Initial investment'},
 ...(a.interim_cash_flows||[]),{date:r.exit_date,amount:r.exit_proceeds,label:'Exit proceeds'}];
flows.forEach((flow,i)=>{
 const row=25+i, [y,m,d]=flow.date.split('-').map(Number);
 formula('A'+row,`=DATE(${y},${m},${d})`);
 s.getRange('A'+row).setNumberFormat('yyyy-mm-dd');
 value('B'+row, Math.max(0,-flow.amount));value('C'+row,Math.max(0,flow.amount));value('E'+row,text(flow.label));
 if(!full&&i===0){formula('A'+row,'=B20');formula('B'+row,'=B11');}
 if(!full&&i===flows.length-1){formula('A'+row,'=B21');formula('C'+row,'=E14');}
 formula('D'+row,`=C${row}-B${row}`);
});
const end=24+flows.length;
formula('B6',`=SUM(B25:B${end})`); formula('B7',`=SUM(C25:C${end})`);
formula('B4','=B7/B6');
if(r.irr != null) formula('B5',`=XIRR(D25:D${end},A25:A${end},${r.irr})`);
else value('B5','No finite XIRR (no distributions)');
value('A'+(end+3),'Scope'); value('B'+(end+3),text(r.warning));
const sources = a.assumption_sources || {};
const sourceSheet = wb.worksheets.add('Sources'); sourceSheet.showGridLines=false;
sourceSheet.getRange('A1:B1').values=[['Input / evidence','Source location']];
Object.entries(sources).slice(0,80).forEach(([key,v],i)=>sourceSheet.getRange(`A${i+2}:B${i+2}`).values=[[text(key),text(typeof v==='object'?JSON.stringify(v):v)]]);
s.getRange('A1:G'+(end+4)).format.font={name:'Arial',size:10};
s.getRange('B10:B19').format.font.color='#0000FF';s.getRange('G10:G29').format.font.color='#0000FF';
s.getRange('A1:G2').format.fill='#173B42';s.getRange('A1:G2').format.font.color='#FFFFFF';
s.getRange('A4:B7').format.fill='#E7F4EF';s.getRange('A4:B7').format.font.bold=true;
s.getRange('A24:E24').format.fill='#DDE8EB';
s.getRange('B4').setNumberFormat('0.00"x"');s.getRange('B5').setNumberFormat('0.0%');
s.getRange('B6:B19').setNumberFormat('#,##0.00;(#,##0.00);"-"');
for(const cell of ['B12','B13','B14','E10','E12','G10:G29'])s.getRange(cell).setNumberFormat('0.0%');
s.getRange(`B25:D${end}`).setNumberFormat('#,##0.00;(#,##0.00);"-"');
s.getRange(`A25:C${end}`).format.font.color='#0000FF';
s.getRange('B20:B21').format.font.color='#0000FF';
if(!full){for(const cell of ['A25','B25','A'+end,'C'+end])s.getRange(cell).format.font.color='#000000';}
s.getRange('A:A').format.columnWidth=33;s.getRange('B:B').format.columnWidth=25;
s.getRange('C:C').format.columnWidth=18;s.getRange('D:D').format.columnWidth=34;s.getRange('E:E').format.columnWidth=27;
s.getRange('A1:G'+(end+4)).format.rowHeight=22;
sourceSheet.getRange('A:A').format.columnWidth=30;sourceSheet.getRange('B:B').format.columnWidth=95;
s.freezePanes.freezeRows(7);
wb.recalculate();
if(preview){const blob=await wb.render({sheetName:'Returns',range:'A1:E21',scale:1.5,format:'png'});await fs.writeFile(preview,new Uint8Array(await blob.arrayBuffer()));}
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
