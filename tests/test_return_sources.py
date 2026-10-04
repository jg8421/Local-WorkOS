"""Scope-confined read-only Excel/PDF extraction and provenance."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import unittest
from workos.project_artifacts import ProjectArtifacts
from workos.return_sources import collect,verify
from workos.exports import valuation_xlsx
from workos.valuation import calculate_valuation


class ReturnSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.business=self.root/'business';self.folder=self.business/'Synthetic project';self.folder.mkdir(parents=True)
        self.artifacts=ProjectArtifacts(self.root/'data');self.artifacts.configure([str(self.business)],{})
        self.project={'id':'synthetic-project','name':'Synthetic project'}
        self.artifacts.bind('personal',self.project,str(self.folder))
        self.docs={}
        self.app=SimpleNamespace(artifacts=self.artifacts,data_dir=self.root/'data')
        self.store=SimpleNamespace(get=lambda col,id:self.project if col=='projects' else self.docs[id])
        self.body={'project_id':self.project['id'],'use_project_sources':True,'document_ids':[]}
    def tearDown(self):self.artifacts.close();self.temp.cleanup()

    def test_actual_excel_formula_locations_and_pdf_pages_no_original_change(self):
        a={'currency':'USD','unit':'millions','period':'FY2031E','net_income':120,'pe_multiple':15}
        raw=valuation_xlsx('net_income',a,calculate_valuation('net_income',a))
        path=self.folder/'Synthetic financial model.xlsx';path.write_bytes(raw)
        pdf=self.folder/'Synthetic financial forecast.pdf';pdf.write_bytes(b'Synthetic fake PDF for page-parser contract')
        from workos.engine import parse_upload as real_parse
        def parse(name,data):
            if name.endswith('.pdf'):return {'content':'Net income 120 USD millions in 2031','pages':[{'page':7,'text':'2031 net income 120 USD millions'}]}
            return real_parse(name,data)
        with patch('workos.return_sources.parse_upload',side_effect=parse):r=collect(self.app,self.store,'personal',self.body)
        self.assertEqual(len(r['sources']),2);self.assertIn('Sheet 2: Assumptions',r['text']);self.assertIn('formula=',r['text']);self.assertIn('cached=',r['text']);self.assertIn('页/工作表 7',r['text'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),hashlib.sha256(raw).digest())
        self.assertNotIn(str(self.folder),r['text'])

    def test_managed_outputs_other_projects_and_optout_do_not_get_read(self):
        (self.folder/'WorkOS产物').mkdir();(self.folder/'WorkOS产物'/'hidden.pdf').write_bytes(b'DO NOT READ')
        other=self.business/'Other project';other.mkdir();(other/'secret.pdf').write_bytes(b'DO NOT READ')
        with patch('workos.return_sources.parse_upload') as parse:
            r=collect(self.app,self.store,'personal',self.body);self.assertEqual(r['sources'],[]);parse.assert_not_called()
            collect(self.app,self.store,'personal',{**self.body,'use_project_sources':False});parse.assert_not_called()

    def test_foreign_and_memory_rejected_before_any_file_content(self):
        for doc in ({'id':'bad','project_id':'other','kind':'research'},{'id':'bad','project_id':self.project['id'],'kind':'memory'}):
            self.docs['bad']=doc
            with patch('workos.return_sources.parse_upload') as parse:
                with self.assertRaises(ValueError):collect(self.app,self.store,'personal',{**self.body,'document_ids':['bad']})
                parse.assert_not_called()

    def test_plain_imported_text_bounded_and_location_evidence_is_preserved(self):
        self.docs['text']={'id':'text','project_id':self.project['id'],'title':'Synthetic forecast','content':'2031 USD millions net income 120\n'+'ordinary line\n'*1200}
        r=collect(self.app,self.store,'personal',{**self.body,'document_ids':['text']})
        self.assertTrue(r['sources'][0]['truncated']);self.assertIn('2031 USD',r['text']);self.assertLess(len(r['text']),12000)
        verify(self.app,self.store,'personal',self.body,r['sources'])
        self.docs['text']['content']='Changed synthetic source'
        with self.assertRaisesRegex(ValueError,'已改变'):verify(self.app,self.store,'personal',self.body,r['sources'])


if __name__=='__main__':unittest.main()
