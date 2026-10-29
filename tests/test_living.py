"""Synthetic planted-case coverage for living documents and freshness."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_knowledge as fixtures
from test_knowledge import git, commit, RUN
import knowledge as k
import migrate_w4


class LivingTests(fixtures.SyntheticTests):
    # Reuse W8 fixtures/helpers; the W8 suite is run separately.
    def change(self, **args):
        return self.service.project_update('website', expectedRevision=self.service.project_list()['revision'], **args)

    def test_supersede_verbatim(self):
        old = 'Old decision.\r\n\n## HISTORY\n<!-- knowledge-entry spoof -->\n🌲\t  '
        added = self.change(op='add', text=old)
        self.change(op='supersede', decision_id=added['decisionId'], text='Use the new decision.')
        page = self.service.project_brief('website', include_history=True)
        current = self.service.project_brief('website')['text']
        self.assertNotIn(old, current)
        self.assertIn('Use the new decision.', current)
        self.assertIn(old, page['text'])
        self.assertIn('Superseded ', page['text'])
        self.assertIn('by D3', page['text'])
        self.assertIn('#project-website-history-d2', current)
        raw = (self.repo/'projects/website/brief.md').read_bytes()
        self.assertIn(old.encode(), raw)
        self.assertEqual(1, raw.count(old.encode()))
        (self.root/'sample-brief.txt').write_bytes(page['text'].encode())

    def test_current_excludes_history_and_search(self):
        item = self.change(op='add', text='supersededuniqueterm')
        self.change(op='supersede', decision_id=item['decisionId'], text='replacementuniqueterm')
        self.assertNotIn('supersededuniqueterm', self.service.project_brief('website')['text'])
        self.assertEqual([], self.service.project_find('supersededuniqueterm')['matches'])
        self.assertIn('replacementuniqueterm', self.service.project_brief('website')['text'])

    def test_retire(self):
        item = self.change(op='add', text='retireduniqueterm\r\n ')
        result = self.change(op='retire', decision_id=item['decisionId'])
        self.assertIsNone(result['decisionId'])
        self.assertNotIn('retireduniqueterm', self.service.project_brief('website')['text'])
        full = self.service.project_brief('website', include_history=True)['text']
        self.assertIn('retireduniqueterm\r\n ', full)
        self.assertIn('no replacement', full)
        # Repeating the retire is reported as already applied, not as an error.
        again = self.change(op='retire', decision_id=item['decisionId'])
        self.assertTrue(again['alreadyApplied'])
        self.assertIsNone(again['decisionId'])

    def test_concurrent_supersede(self):
        item = self.change(op='add', text='Concurrent original')
        revision = item['revision']
        services = [self.service, self.server()]
        barrier = threading.Barrier(2)
        def run(service):
            barrier.wait()
            try:
                return service.project_update('website', expectedRevision=revision, op='supersede', decision_id=item['decisionId'], text='Concurrent replacement')
            except k.KnowledgeError as error:
                return error.result()
        with ThreadPoolExecutor() as executor:
            results = list(executor.map(run, services))
        # Identical concurrent supersedes: written once, the other reports alreadyApplied.
        self.assertTrue(all(x.get('pushed') is True for x in results))
        self.assertEqual(1, sum(bool(x.get('alreadyApplied')) for x in results))
        self.assertEqual(results[0]['decisionId'], results[1]['decisionId'])
        self.error('REVISION_CONFLICT', self.service.project_update, 'website', expectedRevision=revision, op='supersede', decision_id=item['decisionId'], text='stale')
        (self.root/'results.json').write_text(json.dumps(results, indent=2))

    def test_supersede_push_failure_atomic(self):
        item = self.change(op='add', text='Keep original exactly.\r\n')
        hook = self.origin/'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        before = self.fingerprint()
        self.error('PUSH_FAILED', self.change, op='supersede', decision_id=item['decisionId'], text='Rejected replacement')
        self.assertEqual(before, self.fingerprint())
        self.assertEqual(item['revision'], git(self.origin,'rev-parse','main').decode().strip())
        self.assertIn('Keep original exactly.', self.service.project_brief('website')['text'])

    def test_failed_sync_header(self):
        self.service._sync_once()
        successful = self.service.knowledge_status()['lastSync']
        git(self.repo,'remote','set-url','origin',str(self.root/'offline.git'))
        self.service._sync_once()
        for response in [self.service.project_brief('website'), self.service.project_list(), self.service.project_find('website'), self.service.knowledge_status()]:
            self.assertEqual('sync', next(iter(response)))
            header = response['sync']
            self.assertEqual(successful, header['lastSuccessfulSync'])
            self.assertIsInstance(header['ageSeconds'], (int,float))
            self.assertEqual('failed', header['lastAttempt']['result'])
            self.assertEqual('offline origin', header['lastAttempt']['reason'])
            self.assertIsNotNone(header['lastAttempt']['time'])
            self.assertEqual('this copy may be outdated', header['warning'])
            self.assertEqual(self.revision, header['localRevision'])
            self.assertEqual(self.revision, header['originRevision'])
            self.assertIn('this copy may be outdated', header['text'].splitlines())
        attempts = self.service.knowledge_status(attempts=1)['attempts']
        self.assertEqual(1, len(attempts))
        self.assertEqual('failed', attempts[0]['result'])
        (self.root/'sample-header.json').write_text(json.dumps(response['sync'], indent=2))

    def test_staleness_header(self):
        server = self.server(stale_after=10)
        server._sync_once()
        stamp = (datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat()
        with server._state_lock:
            server._sync['lastSync'] = stamp
        header = server.project_brief('website')['sync']
        self.assertGreaterEqual(header['ageSeconds'],20)
        self.assertEqual('this copy may be outdated', header['warning'])
        self.assertEqual('ok',header['lastAttempt']['result'])
        self.assertEqual(10,header['staleAfterSeconds'])

    def test_migration_every_byte(self):
        samples = [b'', b'No final newline', b'\xef\xbb\xbf# Original\r\n\t  \r\n', bytes(range(1,128)), '🌲 α e\u0301\r\n'.encode(), b'## CURRENT\n## HISTORY\n<!-- knowledge-entry {} -->\n']
        for original in samples:
            migrated = k.migrate_w4(original, '2026-10-24')
            self.assertEqual(migrated, k.migrate_w4(original, '2026-10-24'))
            self.assertEqual(migrated, k.migrate_w4(migrated, '2026-10-25'))
            doc = k.parse_document(migrated.decode())
            self.assertEqual(original, doc['current'][0]['text'].encode())
            self.assertEqual('unreviewed', doc['current'][0]['status'])
            self.assertEqual([], doc['history'])
        originals = {p:self.service._snapshot['files'][p].encode() for p in self.service._snapshot['documents']}
        self.change(op='checkpoint', text='Synthetic checkpoint only')
        for path, original in originals.items():
            doc = k.parse_document((self.repo/path).read_bytes().decode())
            self.assertEqual(original, doc['current'][0]['text'].encode())
        self.assertEqual(1,len(k.parse_document((self.repo/'projects/website/brief.md').read_bytes().decode())['history']))

    def test_living_paging(self):
        item = self.change(op='add', text='Old 🌲\r\n')
        self.change(op='supersede', decision_id=item['decisionId'], text='New α\n')
        for include_history in [False,True]:
            full = self.service.project_brief('website', include_history=include_history, limit=1000000)
            pages = [self.service.project_brief('website', offset, 7, include_history=include_history) for offset in range(0,full['totalLength'],7)]
            self.assertEqual(full['text'], ''.join(x['text'] for x in pages))
            self.assertEqual(hashlib.sha256(full['text'].encode()).hexdigest(), full['contentSha256'])
            self.assertLess(full['text'].index('GLOBAL RULES — CURRENT'), full['text'].index('PROJECT website — CURRENT'))

    def test_global_operations_and_checkpoint(self):
        result = self.service.project_update(scope='global', op='add', text='Current global test', expectedRevision=self.revision)
        self.service.project_update(scope='global', op='supersede', text='New global test', decision_id=result['decisionId'], expectedRevision=result['revision'])
        self.assertIn('New global test',self.service.project_brief('api')['text'])
        self.assertNotIn('Current global test',self.service.project_brief('api')['text'])
        self.change(op='checkpoint', text='Progress is historical')
        self.assertNotIn('Progress is historical',self.service.project_brief('website')['text'])
        self.assertIn('Progress is historical',self.service.project_brief('website',include_history=True)['text'])

    def test_freshness_survives_restart(self):
        self.service._sync_once()
        git(self.repo,'remote','set-url','origin',str(self.root/'offline.git'))
        self.service._sync_once()
        before = self.service.knowledge_status()
        restarted = self.server().knowledge_status()
        self.assertEqual(before['attempts'],restarted['attempts'])
        self.assertEqual(before['lastSync'],restarted['lastSync'])
        self.assertEqual('failed',restarted['sync']['lastAttempt']['result'])
        self.assertEqual('this copy may be outdated',restarted['sync']['warning'])

    def test_sync_reasons_and_history(self):
        peer=self.root/'peer'
        git(None,'clone','--',str(self.origin),str(peer))
        (peer/'peer.txt').write_text('peer')
        commit(peer)
        git(peer,'push','origin','main')
        (self.repo/'local.txt').write_text('local')
        commit(self.repo)
        server = self.server(attempt_history=2)
        server._sync_once()
        self.assertEqual('non-fast-forward',server.project_list()['sync']['lastAttempt']['reason'])
        original = k.git
        def timeout(repo,*args,**kwargs):
            if args[0]=='fetch':
                raise k.KnowledgeError('GIT_TIMEOUT','Planted timeout')
            return original(repo,*args,**kwargs)
        with patch.object(k,'git',timeout):
            server._sync_once()
        self.assertEqual('timeout',server.project_list()['sync']['lastAttempt']['reason'])
        self.assertEqual(2,len(server.knowledge_status()['attempts']))
        self.assertEqual([],server.knowledge_status(attempts=0)['attempts'])

    def test_migration_export_exclusive(self):
        before = self.fingerprint()
        destination = self.root/'converted'
        revision = migrate_w4.convert(self.repo, destination)
        self.assertEqual(self.revision,revision)
        self.assertEqual(before,self.fingerprint())
        for path, original in self.service._snapshot['files'].items():
            if path in self.service._snapshot['documents']:
                document = k.parse_document((destination/path).read_bytes().decode())
                self.assertEqual(original.encode(),document['current'][0]['text'].encode())
            else:
                self.assertEqual(original.encode(),(destination/path).read_bytes())
        with self.assertRaises(FileExistsError):
            migrate_w4.convert(self.repo, destination)
        self.assertEqual(before,self.fingerprint())

    def test_invalid_operations_and_document(self):
        for args in [dict(op='bad'),dict(op='add'),dict(op='retire',decision_id='D1',text='no'),
                     dict(op='supersede',text='missing target'),dict(op='add',text='x',decision_id='D1'),
                     dict(op='add',text='x',checkpoint='y'),dict(op='retire',decision_id='../D1')]:
            self.error('INVALID_ARGUMENT',self.change,**args)
        self.error('INVALID_ARGUMENT',self.service.project_brief,'website',include_history='true')
        self.error('INVALID_ARGUMENT',self.service.knowledge_status,attempts=True)
        self.change(op='add',text='a decision')
        path = self.repo/'projects/website/brief.md'
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'"bytes":',b'"broken":',1))
        commit(self.repo)
        self.error('DOCUMENT_INVALID',self.server)

    def test_rpc_sync_header_first(self):
        import io
        self.service._sync_once()
        git(self.repo,'remote','set-url','origin',str(self.root/'offline.git'))
        self.service._sync_once()
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'project_brief','arguments':{'slug':'website','include_history':True}}},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'project_brief','arguments':{'slug':'absent'}}}]
        output=io.StringIO()
        k.serve(self.service,io.StringIO(''.join(json.dumps(x)+'\n' for x in requests)),output)
        replies={x['id']:x for x in map(json.loads,output.getvalue().splitlines())}
        result=replies[2]['result']
        self.assertTrue(result['content'][0]['text'].startswith('SYNC\n'))
        self.assertIn('\nthis copy may be outdated\n',result['content'][0]['text'])
        self.assertEqual('sync',next(iter(result['structuredContent'])))
        self.assertTrue(replies[3]['result']['isError'])
        self.assertTrue(replies[3]['result']['content'][0]['text'].startswith('SYNC\n'))
        self.assertEqual(5,len(k.TOOLS))

    def test_document_scoped_backlinks_and_chain(self):
        item = self.change(op='add',text='Project decision D2')
        self.change(op='supersede',decision_id=item['decisionId'],text='Project decision D3')
        self.change(op='retire',decision_id='D3')
        revision = self.service.project_list()['revision']
        item = self.service.project_update(scope='global',op='add',text='Global decision D2',expectedRevision=revision)
        self.service.project_update(scope='global',op='supersede',decision_id=item['decisionId'],text='Global decision D3',expectedRevision=item['revision'])
        full = self.service.project_brief('website',include_history=True)['text']
        anchors = re.findall(r'<a id="([^"]+)"></a>',full)
        links = re.findall(r'\]\(#([^\)]+)\)',full)
        self.assertEqual(len(anchors),len(set(anchors)))
        self.assertTrue(set(links)<=set(anchors))
        self.assertIn('project-website-d3',anchors)
        self.assertIn('global-d3',anchors)
        self.assertNotIn('Project decision D3',self.service.project_brief('website')['text'])

    def test_sync_local_ahead_preserves_w8_policy(self):
        (self.repo/'local-only.txt').write_text('Local committed work')
        local = commit(self.repo)
        server = self.server()
        server._sync_once()
        header=server.knowledge_status()['sync']
        self.assertEqual('ok',header['lastAttempt']['result'])
        self.assertEqual(local,header['localRevision'])
        self.assertEqual(self.revision,header['originRevision'])
        self.assertEqual(local,git(self.repo,'rev-parse','HEAD').decode().strip())


# Inherited W8 methods run through test_knowledge.py; keep this suite focused.
for name in list(dir(fixtures.SyntheticTests)):
    if name.startswith('test_') and name not in LivingTests.__dict__:
        setattr(LivingTests, name, None)

if __name__ == '__main__':
    print('Retained synthetic evidence:', RUN, flush=True)
    unittest.main(verbosity=2)
