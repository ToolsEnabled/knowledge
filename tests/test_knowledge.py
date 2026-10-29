"""Synthetic-only integration and contract tests; retain every fixture and trace."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import knowledge as k

RUN = ROOT/'evidence'/'runs'/str(time.time_ns())
RUN.mkdir(parents=True)


def git(repo,*args):
    return k.git(repo,*args,timeout=5)


def commit(repo,message='fixture'):
    git(repo,'add','.')
    git(repo,'-c','user.name=Fixture','-c','user.email=fixture@localhost','commit','--no-verify','-m',message)
    return git(repo,'rev-parse','HEAD').decode().strip()


class SyntheticTests(unittest.TestCase):
    def setUp(self):
        self.root = RUN/self._testMethodName
        self.root.mkdir()
        self.origin,self.repo = self.root/'origin.git',self.root/'repo'
        git(None,'init','--bare','--initial-branch=main',str(self.origin))
        git(None,'clone','--',str(self.origin),str(self.repo))
        (self.repo/'rules').mkdir()
        (self.repo/'rules/global.md').write_bytes('Rules α🌲\r\nDo careful work.\n'.encode())
        records = [
            ('website','Public website and accessible landing pages','Use semantic HTML.\n'),
            ('api','Backend service and API','Implement endpoint authentication.\n'),
            ('website-lab','Experimental website sandbox','Experiments only.\n')]
        for slug,summary,text in records:
            path = self.repo/'projects'/slug
            path.mkdir(parents=True)
            (path/'brief.md').write_bytes(text.encode())
        (self.repo/'INDEX.md').write_text('# Synthetic briefs\n\n'+''.join(
            f'- `{slug}` (active): {summary}; laptop:/synthetic/briefs/projects/{slug}/brief.md; desktop:/synthetic/other/{slug}\n'
            for slug,summary,text in records)+'- `retired` (archive): Old project; laptop:/synthetic/archive/retired.md\n')
        (self.repo/'MANIFEST.json').write_text(json.dumps({'version':1,'projects':[{'slug':s,'archived':False} for s,_,_ in records]}))
        self.revision = commit(self.repo)
        git(self.repo,'push','origin','main')
        self.service = self.server()

    def server(self,**kwargs):
        return k.Knowledge(k.Config.load({'repo':str(self.repo),'sync':'off',**kwargs}),check_guard=False)

    def error(self,code,fn,*args,**kwargs):
        with self.assertRaises(k.KnowledgeError) as cm:
            fn(*args,**kwargs)
        self.assertEqual(code,cm.exception.code)
        return cm.exception

    def fingerprint(self):
        return {'head':git(self.repo,'rev-parse','HEAD').decode(),
                'refs':git(self.repo,'show-ref').decode(),
                'index':hashlib.sha256((self.repo/'.git/index').read_bytes()).hexdigest(),
                'files':{str(p.relative_to(self.repo)):hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in self.repo.rglob('*') if p.is_file() and '.git' not in p.relative_to(self.repo).parts},
                'status':git(self.repo,'status','--porcelain').decode()}

    def test_list_index(self):
        result = self.service.project_list()
        self.assertEqual(3,result['total'])
        self.assertEqual('Public website and accessible landing pages',result['projects'][1]['summary'])
        self.assertEqual({'desktop','laptop'},set(result['projects'][1]['locations']))
        self.assertNotIn('retired',[p['slug'] for p in result['projects']])

    def test_rank_explained_deterministic(self):
        result = self.service.project_find('please work on website')
        self.assertEqual('website',result['matches'][0]['slug'])
        self.assertEqual(result,self.service.project_find('please work on website'))
        for item in result['matches']:
            self.assertEqual(item['score'],sum(x['points'] for x in item['reasons']))
        self.assertEqual([],self.service.project_find('unmatchedxyz')['matches'])
        self.error('INVALID_ARGUMENT',self.service.project_find,' ')

    def test_paging_boundaries(self):
        original = self.service.project_brief('website',limit=1000000)['text']
        self.assertIn((self.repo/'rules/global.md').read_bytes().decode(), original)
        self.assertIn((self.repo/'projects/website/brief.md').read_bytes().decode(), original)
        pieces = []
        offset=0
        while True:
            page=self.service.project_brief('website',offset,1)
            self.assertEqual(offset,page['offset'])
            self.assertEqual(len(original),page['totalLength'])
            self.assertEqual(1,page['returnedLength'])
            pieces.append(page['text'])
            if page['nextOffset'] is None:
                break
            self.assertEqual(offset+1,page['nextOffset'])
            offset=page['nextOffset']
        self.assertEqual(original,''.join(pieces))
        self.assertEqual('',self.service.project_brief('website',len(original),5)['text'])
        self.error('OFFSET_OUT_OF_RANGE',self.service.project_brief,'website',len(original)+1)
        for offset,limit in [(-1,1),(0,0),(True,2),(0,False),(1.1,5)]:
            self.error('INVALID_ARGUMENT',self.service.project_brief,'website',offset,limit)
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),page['contentSha256'])

    def test_unknown_slug(self):
        self.error('UNKNOWN_PROJECT',self.service.project_brief,'absent')
        self.error('UNKNOWN_PROJECT',self.service.project_update,'absent','checkpoint',self.revision)
        self.error('INVALID_ARGUMENT',self.service.project_brief,'../../escape')
        self.error('UNKNOWN_PROJECT',self.service.project_brief,'retired')

    def test_huge_brief(self):
        content='🌲 website data\r\n'*200000+'END_SENTINEL'
        path=self.repo/'projects/website/brief.md'
        path.write_bytes(content.encode())
        commit(self.repo)
        server=self.server()
        total=server.project_brief('website')['totalLength']
        chunks=[]
        for offset in range(0,total,65537):
            chunks.append(server.project_brief('website',offset,65537)['text'])
        expected=server.project_brief('website',limit=total)['text']
        self.assertEqual(expected,''.join(chunks))
        self.assertIn(content,expected)
        self.assertIn('END_SENTINEL',chunks[-1])
        self.assertGreater(total,3000000)

    def test_update_success(self):
        before=(self.repo/'projects/website/brief.md').read_bytes()
        manifest=(self.repo/'MANIFEST.json').read_bytes()
        result=self.service.project_update('website','Verified synthetic landing page. 🌲',self.revision)
        self.assertTrue(result['pushed'] and result['localApplied'])
        self.assertEqual(result['revision'],git(self.origin,'rev-parse','main').decode().strip())
        self.assertEqual(result['revision'],git(self.repo,'rev-parse','HEAD').decode().strip())
        self.assertEqual(manifest,(self.repo/'MANIFEST.json').read_bytes())
        document=k.parse_document((self.repo/'projects/website/brief.md').read_bytes().decode())
        self.assertEqual(before,document['current'][0]['text'].encode())
        self.assertEqual('Verified synthetic landing page. 🌲',document['history'][0]['text'])
        self.assertEqual('checkpoint',document['history'][0]['kind'])
        self.assertEqual(b'',git(self.repo,'status','--porcelain'))
        record=json.loads((self.repo/'CHECKPOINTS.jsonl').read_text())
        self.assertEqual(self.revision,record['baseRevision'])

    def test_concurrent_conflict(self):
        services=[self.service,self.server()]
        barrier=threading.Barrier(2)
        def update(item):
            service,text=item
            barrier.wait()
            try:
                return service.project_update('website',text,self.revision)
            except k.KnowledgeError as e:
                return e.result()
        with ThreadPoolExecutor() as executor:
            results=list(executor.map(update,zip(services,['Concurrent checkpoint A','Concurrent checkpoint B'])))
        (self.root/'concurrent.json').write_text(json.dumps(results,indent=2))
        self.assertEqual(1,sum(x.get('pushed') is True for x in results))
        self.assertEqual(1,sum(x.get('error')=='REVISION_CONFLICT' for x in results))
        conflict=next(x for x in results if x.get('error'))
        self.assertEqual('projects/website/brief.md',conflict['path'])
        self.error('REVISION_CONFLICT',self.service.project_update,'website','stale',self.revision)
        self.assertEqual(1,len(k.parse_document((self.repo/'projects/website/brief.md').read_bytes().decode())['history']))
        # The conflict names the revision to retry with; that retry succeeds.
        retry=self.service.project_update('website','Concurrent checkpoint after reread',conflict['revision'])
        self.assertTrue(retry['pushed'])

    def test_concurrent_identical_update_written_once(self):
        services=[self.service,self.server()]
        barrier=threading.Barrier(2)
        def update(service):
            barrier.wait()
            return service.project_update('website','Same checkpoint twice',self.revision)
        with ThreadPoolExecutor() as executor:
            results=list(executor.map(update,services))
        self.assertEqual(1,sum(bool(x.get('alreadyApplied')) for x in results))
        self.assertTrue(all(x['pushed'] for x in results))
        self.assertEqual(1,len(k.parse_document((self.repo/'projects/website/brief.md').read_bytes().decode())['history']))

    def test_push_rejection_no_partial(self):
        hook=self.origin/'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        before=self.fingerprint()
        error=self.error('PUSH_FAILED',self.service.project_update,'website','Rejected checkpoint',self.revision)
        after=self.fingerprint()
        self.assertEqual(before,after)
        self.assertEqual(self.revision,git(self.origin,'rev-parse','main').decode().strip())
        (self.root/'proof.json').write_text(json.dumps({'error':error.result(),'before':before,'after':after},indent=2))

    def test_offline_update_no_partial(self):
        git(self.repo,'remote','set-url','origin',str(self.root/'offline.git'))
        before=self.fingerprint()
        self.error('PUSH_FAILED',self.service.project_update,'website','offline',self.revision)
        self.assertEqual(before,self.fingerprint())

    def test_remote_advanced_unrelated_change_still_updates(self):
        # Another machine pushed an unrelated change after this clone's last sync: the update catches up and lands on top.
        peer=self.root/'peer'
        git(None,'clone','--',str(self.origin),str(peer))
        (peer/'external.txt').write_text('Peer work retained.\n')
        remote=commit(peer)
        git(peer,'push','origin','main')
        original=k.git
        pushes=[]
        def counting(repo,*args,**kwargs):
            if args and args[0]=='push':
                pushes.append(args)
            return original(repo,*args,**kwargs)
        with patch.object(k,'git',counting):
            result=self.service.project_update('website','Lands after peer push',self.revision)
        self.assertTrue(result['pushed'] and result['localApplied'])
        self.assertEqual(1,len(pushes))  # caught up before pushing, so no rejected push first
        head=git(self.origin,'rev-parse','main').decode().strip()
        self.assertEqual(result['revision'],head)
        self.assertEqual(remote,git(self.origin,'rev-parse','main^').decode().strip())
        self.assertEqual('Peer work retained.\n',(self.repo/'external.txt').read_text())
        self.assertEqual(b'',git(self.repo,'status','--porcelain'))

    def test_remote_changed_same_brief_conflicts(self):
        peer=self.root/'peer'
        git(None,'clone','--',str(self.origin),str(peer))
        peer_service=k.Knowledge(k.Config.load({'repo':str(peer),'sync':'off'}),check_guard=False)
        landed=peer_service.project_update('website',op='add',text='Peer decision',expectedRevision=self.revision)
        before=git(self.origin,'rev-parse','main').decode().strip()
        error=self.error('REVISION_CONFLICT',self.service.project_update,'website','Mine',self.revision)
        self.assertEqual(landed['revision'],error.details['revision'])
        self.assertIn('Peer decision',[x['text'] for x in error.details['changed']])
        self.assertEqual(before,git(self.origin,'rev-parse','main').decode().strip())
        retry=self.service.project_update('website','Mine',error.details['revision'])
        self.assertTrue(retry['pushed'])

    def test_push_race_rebuilds_on_top(self):
        # Another machine pushes between this update's catch-up and its push: retried on top, nothing lost.
        peer=self.root/'peer'
        git(None,'clone','--',str(self.origin),str(peer))
        original=k.git
        raced=[]
        def racing(repo,*args,**kwargs):
            if args and args[0]=='push' and not raced:
                raced.append(1)
                (peer/'race.txt').write_text('raced\n')
                commit(peer)
                original(peer,'push','origin','main',timeout=5)
            return original(repo,*args,**kwargs)
        with patch.object(k,'git',racing):
            result=self.service.project_update('website','After the race',self.revision)
        self.assertTrue(result['pushed'])
        self.assertEqual('raced\n',(self.repo/'race.txt').read_text())
        self.assertEqual(result['revision'],git(self.origin,'rev-parse','main').decode().strip())
        self.assertEqual(1,len(k.parse_document((self.repo/'projects/website/brief.md').read_bytes().decode())['history']))

    def test_lock_waits_for_other_process(self):
        import fcntl
        lock=open(self.repo/'.git/knowledge.lock','a+b')
        fcntl.flock(lock,fcntl.LOCK_EX)
        threading.Timer(0.5,lambda:(fcntl.flock(lock,fcntl.LOCK_UN),lock.close())).start()
        started=time.monotonic()
        result=self.server(lock_wait=5).project_update('website','Waited for the lock',self.revision)
        self.assertTrue(result['pushed'])
        self.assertGreater(time.monotonic()-started,0.4)
        holder=open(self.repo/'.git/knowledge.lock','a+b')
        fcntl.flock(holder,fcntl.LOCK_EX)
        try:
            self.error('REPO_BUSY',self.server(lock_wait=0.2).project_update,'website','Lock never freed',result['revision'])
        finally:
            fcntl.flock(holder,fcntl.LOCK_UN); holder.close()

    def test_update_leaves_no_staging_copies(self):
        self.service.project_update('website','No clones left behind',self.revision)
        self.assertEqual([],[p.name for p in self.repo.parent.iterdir() if p.name.startswith('.knowledge-')])
        self.assertEqual([],[p.name for p in (self.repo/'.git').iterdir() if p.name.startswith('knowledge-index-')])

    def test_reads_follow_head_moved_by_other_process(self):
        other=self.server()
        self.service.project_brief('website')
        landed=other.project_update('website',op='add',text='Seen by the other reader',expectedRevision=self.revision)
        page=self.service.project_brief('website')
        self.assertEqual(landed['revision'],page['revision'])
        self.assertIn('Seen by the other reader',page['text'])

    def test_first_read_waits_for_running_sync(self):
        peer=self.root/'peer'
        git(None,'clone','--',str(self.origin),str(peer))
        (peer/'projects/website/brief.md').write_text('Newer peer page\n')
        revision=commit(peer)
        git(peer,'push','origin','main')
        server=self.server(sync='background',timeout=5,interval=60,read_wait=5)
        page=server.project_brief('website')
        self.assertEqual(revision,page['revision'])
        self.assertIsNone(page['sync']['warning'])

    def test_stale_read_wait_is_bounded(self):
        scripts=self.root/'bin'
        scripts.mkdir()
        wrapper=scripts/'git'
        wrapper.write_text('#!/bin/sh\nfor arg in "$@"; do\nif [ "$arg" = fetch ]; then sleep 30; exit 1; fi\ndone\nexec /usr/bin/git "$@"\n')
        wrapper.chmod(0o700)
        with patch.dict(os.environ,{'PATH':str(scripts)+':'+os.environ['PATH']}):
            server=self.server(sync='background',timeout=10,interval=60,read_wait=0.5)
            started=time.monotonic()
            page=server.project_brief('website')
            elapsed=time.monotonic()-started
        self.assertLess(elapsed,2)
        self.assertEqual(self.revision,page['revision'])
        self.assertEqual('this copy may be outdated',page['sync']['warning'])

    def test_dirty_repo_refused(self):
        path=self.repo/'projects/website/brief.md'
        path.write_text('uncommitted owner edit')
        before=self.fingerprint()
        self.error('DIRTY_REPO',self.service.project_update,'website','no',self.revision)
        self.assertEqual(before,self.fingerprint())

    def test_offline_sync_keeps_snapshot(self):
        git(self.repo,'remote','set-url','origin',str(self.root/'offline.git'))
        server=self.server()
        before=server.project_brief('website')
        started=time.monotonic()
        server._sync_once()
        elapsed=time.monotonic()-started
        after=server.project_brief('website')
        self.assertEqual({key:value for key,value in before.items() if key!='sync'},
                         {key:value for key,value in after.items() if key!='sync'})
        state=server.knowledge_status()
        self.assertEqual('failed',state['state'])
        self.assertIsNone(state['lastSync'])
        (self.root/'proof.json').write_text(json.dumps({'elapsed':elapsed,'status':state,'snapshotRetained':True},indent=2))

    def test_ff_sync(self):
        peer=self.root/'peer'
        git(None,'clone','--',str(self.origin),str(peer))
        (peer/'projects/website/brief.md').write_text('New synthetic page\n')
        revision=commit(peer)
        git(peer,'push','origin','main')
        self.service._sync_once()
        state=self.service.knowledge_status()
        self.assertEqual(revision,state['revision'])
        self.assertEqual('ok',state['state'])
        self.assertIsNotNone(state['lastSync'])

    def test_sync_divergence(self):
        peer=self.root/'peer'
        git(None,'clone','--',str(self.origin),str(peer))
        (peer/'peer.txt').write_text('peer')
        commit(peer)
        git(peer,'push','origin','main')
        (self.repo/'local.txt').write_text('local')
        commit(self.repo)
        before=self.fingerprint()
        self.service._sync_once()
        self.assertEqual('failed',self.service.knowledge_status()['state'])
        after=self.fingerprint()
        self.assertEqual(before['head'],after['head'])
        self.assertEqual(before['files'],after['files'])
        self.assertEqual(before['index'],after['index'])

    def test_background_timeout_nonblocking(self):
        scripts=self.root/'bin'
        scripts.mkdir()
        wrapper=scripts/'git'
        wrapper.write_text('#!/bin/sh\nfor arg in "$@"; do\nif [ "$arg" = fetch ]; then sleep 30; exit 1; fi\ndone\nexec /usr/bin/git "$@"\n')
        wrapper.chmod(0o700)
        with patch.dict(os.environ,{'PATH':str(scripts)+':'+os.environ['PATH']}):
            server=self.server(sync='background',timeout=0.3,interval=60,read_wait=0)
            started=time.monotonic()
            page=server.project_brief('website')
            state=server.knowledge_status()
            call_elapsed=time.monotonic()-started
            self.assertLess(call_elapsed,0.15)
            server._sync_thread.join(3)
            state=server.knowledge_status()
        self.assertFalse(server._sync_thread.is_alive())
        self.assertEqual('failed',state['state'])
        self.assertEqual('GIT_TIMEOUT',state['error'])
        self.assertEqual(self.revision,page['revision'])
        (self.root/'proof.json').write_text(json.dumps({'readSeconds':call_elapsed,'status':state},indent=2))

    def test_origin_only_and_first_clone(self):
        config=k.Config.load({'origin':str(self.origin),'repo':str(self.root/'fresh'),'sync':'background'})
        server=k.Knowledge(config,check_guard=False)
        server._sync_thread.join(5)
        self.assertTrue(server.knowledge_status()['ready'])
        self.assertEqual(3,server.project_list()['total'])
        config=k.Config.load({'origin':str(self.root/'absent.git'),'repo':str(self.root/'absent'),'sync':'background'})
        server=k.Knowledge(config,check_guard=False)
        server._sync_thread.join(5)
        self.assertFalse(server.knowledge_status()['ready'])
        self.error('REPO_NOT_READY',server.project_list)

    def test_interior_marker_refusal(self):
        for marker in k.MARKERS:
            self.error('INTERIOR_WORKER_REFUSED',k.guard,environ={marker:'1'})
        config=self.root/'config.json'
        config.write_text(json.dumps({'repo':str(self.root/'MUST_NOT_OPEN'),'sync':'off'}))
        env=dict(os.environ,TOOLSENABLED_FLEET_WORKER='1')
        process=subprocess.run([sys.executable,str(ROOT/'knowledge.py'),'--owner-launch','--config',str(config)],
                               input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',text=True,capture_output=True,env=env)
        self.assertEqual(2,process.returncode)
        self.assertEqual('',process.stdout)
        self.assertIn('INTERIOR_WORKER_REFUSED',process.stderr)
        (self.root/'proof.json').write_text(json.dumps({'exit':process.returncode,'stdout':process.stdout,'stderr':process.stderr},indent=2))

    def test_interior_ancestor_and_policy(self):
        proc=self.root/'proc'
        for pid,comm,parent in [(900,'python3',800),(800,'fleet-worker',1)]:
            directory=proc/str(pid)
            directory.mkdir(parents=True)
            (directory/'comm').write_text(comm)
            (directory/'stat').write_text(f'{pid} ({comm}) S {parent} 0 0')
        self.error('INTERIOR_WORKER_REFUSED',k.guard,environ={},proc=proc,start_pid=900)
        k.guard('allow',environ={'TOOLSENABLED_FLEET_WORKER':'1'})
        self.error('INTERIOR_IDENTITY_UNKNOWN',k.guard,environ={},proc=proc,start_pid=999)

    def test_symlink_layout_rejected(self):
        target=self.repo/'projects/website/brief.md'
        target.rename(self.repo/'retained-original.txt')
        target.symlink_to('../../retained-original.txt')
        commit(self.repo)
        self.error('LAYOUT_INVALID',self.server)

    def test_read_committed_snapshot(self):
        before=self.service.project_brief('website')
        (self.repo/'projects/website/brief.md').write_text('uncommitted different content')
        self.assertEqual(before,self.service.project_brief('website'))
        self.assertEqual(before,self.server().project_brief('website'))

    def test_rpc_contract(self):
        config=self.root/'config.json'
        config.write_text(json.dumps({'repo':str(self.repo),'sync':'off','interior':'allow'}))
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'project_brief','arguments':{'slug':'website','limit':10}}},
                  {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'project_brief','arguments':{'slug':'unknown'}}},
                  {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'knowledge_status','arguments':{'extra':1}}}]
        # In-memory protocol probe avoids changing real worker ancestry/guard.
        import io
        output=io.StringIO()
        k.serve(self.service,io.StringIO(''.join(json.dumps(x)+'\n' for x in requests)),output)
        responses={x['id']:x for x in map(json.loads,output.getvalue().splitlines())}
        self.assertEqual('knowledge',responses[1]['result']['serverInfo']['name'])
        self.assertEqual('2025-06-18',responses[1]['result']['protocolVersion'])
        self.assertEqual(5,len(responses[2]['result']['tools']))
        self.assertEqual(10,responses[3]['result']['structuredContent']['returnedLength'])
        self.assertTrue(responses[4]['result']['isError'])
        self.assertTrue(responses[5]['result']['isError'])
        (self.root/'transcript.json').write_text(json.dumps(responses,indent=2))

    def test_configuration(self):
        for value in [{},{'repo':str(self.repo),'extra':1},{'repo':str(self.repo),'sync':'unsafe'},
                      {'repo':str(self.repo),'timeout':0},{'repo':str(self.repo),'interval':float('nan')},
                      {'origin':'https://user:' + ('x' * 8) + '@example.test/repo'}]:
            self.error('CONFIG_INVALID',k.Config.load,value)
        with patch.dict(os.environ,{'XDG_CACHE_HOME':str(self.root/'cache')}):
            self.assertTrue(str(k.Config.load({'origin':str(self.origin)}).repo).startswith(str(self.root/'cache/knowledge')))



    def test_push_timeout_is_uncertain(self):
        original=k.git
        def timeout(repo,*args,**kwargs):
            if args[0]=='push':
                raise k.KnowledgeError('GIT_TIMEOUT','planted transport uncertainty')
            return original(repo,*args,**kwargs)
        before=self.fingerprint()
        with patch.object(k,'git',timeout):
            result=self.error('PUSH_OUTCOME_UNKNOWN',self.service.project_update,'website','timeout',self.revision)
        self.assertEqual(before,self.fingerprint())
        git(self.repo,'cat-file','-e',result.details['stagedRevision'])
        # Retrying the same update is safe and writes it once.
        retry=self.service.project_update('website','timeout',self.revision)
        self.assertTrue(retry['pushed'])
        retry=self.service.project_update('website','timeout',self.revision)
        self.assertTrue(retry['alreadyApplied'])
        self.assertEqual(1,len(k.parse_document((self.repo/'projects/website/brief.md').read_bytes().decode())['history']))

    def test_push_timeout_that_landed_is_success(self):
        original=k.git
        def slow(repo,*args,**kwargs):
            if args and args[0]=='push':
                original(repo,*args,**kwargs)
                raise k.KnowledgeError('GIT_TIMEOUT','planted: push landed but the transport timed out')
            return original(repo,*args,**kwargs)
        with patch.object(k,'git',slow):
            result=self.service.project_update('website','landed despite timeout',self.revision)
        self.assertTrue(result['pushed'] and result['localApplied'])
        self.assertEqual(result['revision'],git(self.origin,'rev-parse','main').decode().strip())

    def test_remote_success_local_refresh_error(self):
        original=k.git
        def failure(repo,*args,**kwargs):
            if repo==self.repo and args[0]=='merge':
                raise k.KnowledgeError('GIT_FAILED','planted local refresh error')
            return original(repo,*args,**kwargs)
        with patch.object(k,'git',failure):
            result=self.error('PUSHED_LOCAL_REFRESH_REQUIRED',self.service.project_update,'website','remote accepted',self.revision)
        self.assertEqual(result.details['revision'],git(self.origin,'rev-parse','main').decode().strip())
        self.assertEqual(self.revision,git(self.repo,'rev-parse','HEAD').decode().strip())

    def test_relative_origin(self):
        git(self.repo,'remote','set-url','origin','../origin.git')
        server=self.server()
        result=server.project_update('website','relative origin works',self.revision)
        self.assertTrue(result['pushed'])

    def test_rpc_invalid_params(self):
        import io
        output=io.StringIO()
        k.serve(self.service,io.StringIO('{invalid\n[]\n{"jsonrpc":"2.0","id":1,"method":"initialize","params":[]}\n'),output)
        replies=list(map(json.loads,output.getvalue().splitlines()))
        self.assertEqual([-32700,-32600,-32602],[x['error']['code'] for x in replies])

    def test_ignored_local_file_not_overwritten(self):
        # info/exclude is clone-local, so the staged clone would not inherit it.
        (self.repo/'.git/info/exclude').write_text('CHECKPOINTS.jsonl\n')
        audit=self.repo/'CHECKPOINTS.jsonl'
        audit.write_text('Untracked owner data must survive.\n')
        before=audit.read_bytes()
        self.error('PUSHED_LOCAL_REFRESH_REQUIRED',self.service.project_update,'website','new checkpoint',self.revision)
        self.assertEqual(before,audit.read_bytes())
        self.assertEqual(self.revision,git(self.repo,'rev-parse','HEAD').decode().strip())

    def test_existing_unsafe_origin_not_exposed(self):
        for origin in ('https://support@toolsenabled.ai/repo','ssh://fixture:support@toolsenabled.ai/repo'):
            git(self.repo,'remote','set-url','origin',origin)
            result=self.error('ORIGIN_UNSAFE',self.server)
            self.assertNotIn('synthetic-token',str(result))
            self.assertNotIn('synthetic-password',str(result))

if __name__=='__main__':
    print('Retained synthetic evidence:',RUN,flush=True)
    unittest.main(verbosity=2)
