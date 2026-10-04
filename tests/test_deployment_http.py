"""Missing optional libraries remain a setup issue, never a destructive export."""
import builtins
import http.client
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from workos.server import Application, Handler, LocalServer
from workos.valuation import calculate_valuation


class PortableDependencyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ,{'WORKOS_PUBLIC_ORIGIN':'','WORKOS_SYNC_ROOT':'','WORKOS_MEMORY_ROOT':''}), \
                patch('workos.server.find_root',return_value=None):
            self.app=Application(Path(self.tmp.name)/'synthetic-data',port=0)
        self.addCleanup(self.app.close)
        self.server=LocalServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.app.port=self.server.server_address[1];self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        self.thread.start();self.addCleanup(self.stop_server)
        self.store=self.app.stores['personal']
        self.project=self.store.create('projects',{'name':'Synthetic dependency project'})
        self.record=self.store.create('deliverables',{'title':'Synthetic retained report','body':'Synthetic retained text',
                'project_id':self.project['id'],'kind':'自定义'})
        self.meeting=self.store.create('meetings',{'title':'Synthetic retained meeting','summary':'Synthetic summary',
                'transcript':'Synthetic transcript','project_id':self.project['id']})
        assumptions={'currency':'USD','unit':'millions','period':'FY2025A','net_income':100,'pe_multiple':12}
        self.model=self.store.create('deliverables',{'title':'Synthetic retained model','body':'Synthetic conditions',
                'project_id':self.project['id'],'kind':'自定义','method':'net_income','assumptions':assumptions,
                'result':calculate_valuation('net_income',assumptions)})

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def get(self,path):
        connection=http.client.HTTPConnection('127.0.0.1',self.app.port,timeout=3)
        try:
            connection.request('GET',path,headers={'X-Workspace':'personal'})
            response=connection.getresponse();raw=response.read()
            return response.status,json.loads(raw) if response.getheader('Content-Type','').startswith('application/json') else raw
        finally:connection.close()

    def test_missing_optional_export_components_are_setup_errors_and_keep_records(self):
        cases=[('docx','deliverables',self.record,'/api/export/'+self.record['id']+'?format=docx'),
               ('pptx','deliverables',self.record,'/api/export/'+self.record['id']+'?format=pptx'),
               ('openpyxl','deliverables',self.model,'/api/export/'+self.model['id']+'?format=xlsx'),
               ('docx','meetings',self.meeting,'/api/meeting-export/'+self.meeting['id']+'?format=docx')]
        original_import=builtins.__import__
        for dependency,collection,record,path in cases:
            with self.subTest(dependency=dependency,path=path):
                before=self.store.get(collection,record['id'])
                def unavailable(name,*args,**kwargs):
                    if name.split('.')[0]==dependency:raise ModuleNotFoundError('Synthetic optional dependency absent',name=name)
                    return original_import(name,*args,**kwargs)
                with patch('builtins.__import__',side_effect=unavailable):status,response=self.get(path)
                self.assertEqual(status,400,response);self.assertEqual(response.get('code'),'needs_setup',response)
                self.assertIn('完整版',response['error']);self.assertIn('HTML',response['error'])
                self.assertNotIn('pip',response['error']);self.assertNotIn('Traceback',response['error'])
                self.assertEqual(self.store.get(collection,record['id']),before)
        status,html=self.get('/api/export/'+self.record['id']+'?format=html')
        self.assertEqual(status,200);self.assertIn(b'Synthetic retained text',html)

    def test_unexpected_internal_import_error_remains_failure_without_setup_claim(self):
        before=self.store.get('deliverables',self.record['id'])
        with self.assertLogs(level='ERROR'),patch('workos.server.docx_report',side_effect=ImportError('Synthetic internal bug',name='workos_internal_bug')):
            status,response=self.get('/api/export/'+self.record['id']+'?format=docx')
        self.assertEqual(status,500,response);self.assertNotIn('code',response)
        self.assertNotIn('Synthetic internal bug',response['error'])
        self.assertEqual(self.store.get('deliverables',self.record['id']),before)
