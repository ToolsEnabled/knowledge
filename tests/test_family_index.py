"""Real W4 extractor, synthetic inputs only; every fixture is retained."""
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path, loader=SourceFileLoader(name, str(path)))
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


k = module('family_index_knowledge', Path(os.environ.get('KNOWLEDGE_UNDER_TEST', ROOT/'knowledge.py')))
EXTRACTOR = Path(os.environ['FAMILY_INDEX_EXTRACTOR']) if os.environ.get('FAMILY_INDEX_EXTRACTOR') else None
if EXTRACTOR is None or not EXTRACTOR.is_file():
    raise unittest.SkipTest('Optional family-index integration source is not included in this export')
b = module('family_index_extractor', EXTRACTOR)
RUN = Path(os.environ.get('FAMILY_INDEX_EVIDENCE', ROOT/'evidence'/('family-index-'+str(time.time_ns()))))
RUN.mkdir(parents=True)
(RUN/'extractor-sha256.txt').write_text(hashlib.sha256(EXTRACTOR.read_bytes()).hexdigest()+'\n')
DATE = '2026-10-24T00:00:00+00:00'


class FamilyIndex(unittest.TestCase):
    def setUp(self):
        self.root = RUN/self._testMethodName
        self.root.mkdir()
        source = self.root/'source.synthetic.md'
        source.write_text('## Standing rules\nSynthetic global rule.\n## Projects\n'
                          '| **Alpha** | synthetic | active quasar |\n'
                          '| **Retired** | synthetic | done |\n'
                          '| **Unknown title** | synthetic | active |\n'
                          '| **`literal`: Synthetic** | synthetic | active |\n')
        mapping = self.root/'mapping.synthetic.json'
        mapping.write_text(json.dumps({'mapping':{'Alpha':'alpha','Retired':'archive','Empty':'empty'}}))
        self.repo = self.root/'repo'
        b.extract_family({'host-a':source}, self.repo, mapping)
        self.index = self.repo/'INDEX.md'
        k.git(None, 'init', '--initial-branch=main', str(self.repo))
        self.commit()

    def commit(self):
        k.git(self.repo, 'add', '.')
        k.git(self.repo, '-c', 'user.name=Synthetic', '-c', 'user.email=fixture@localhost',
              'commit', '--allow-empty', '--no-verify', '-m', 'Synthetic family fixture')

    def server(self):
        self.commit()
        return k.Knowledge(k.Config.load({'repo':str(self.repo),'sync':'off'}), check_guard=False)

    def invalid(self, row):
        self.index.write_text('# Synthetic INDEX\n'+row+'\n')
        with self.assertRaises(k.KnowledgeError) as cm:
            self.server()
        self.assertEqual('LAYOUT_INVALID', cm.exception.code)
        self.assertIn(row, str(cm.exception))

    def test_real_family_extractor(self):
        server = self.server()
        projects = {p['slug']:p for p in server.project_list()['projects']}
        self.assertEqual({'alpha','empty'}, set(projects))
        self.assertEqual('| **Alpha** | synthetic | active quasar |', projects['alpha']['summary'])
        self.assertEqual('', projects['empty']['summary'])
        self.assertEqual({}, projects['alpha']['locations'])
        match = server.project_find('quasar')['matches'][0]
        self.assertEqual('alpha', match['slug'])
        self.assertTrue(any(r['field']=='summary' for r in match['reasons']))
        self.assertIn('active quasar', server.project_brief('alpha')['text'])

    def test_mixed_old_and_new(self):
        text = self.index.read_text().replace('- `empty`: [projects/empty/brief.md](projects/empty/brief.md)',
                  '- `empty` (active): Original summary; host:/synthetic/empty')
        self.index.write_text(text)
        projects = {p['slug']:p for p in self.server().project_list()['projects']}
        self.assertEqual('Original summary', projects['empty']['summary'])
        self.assertEqual({'host':'/synthetic/empty'}, projects['empty']['locations'])

    def test_current_summary_excludes_history(self):
        path = self.repo/'projects/alpha/brief.md'
        path.write_text(k.render_document({'current':[{'id':'D2','date':DATE,'status':'reviewed',
                             'text':'Current nebula guidance.\nSecond line.'}],
                      'history':[{'id':'D1','date':DATE,'status':'reviewed','text':'Obsolete sentinel.',
                                  'supersededAt':DATE,'supersededBy':'D2'}]}))
        server = self.server()
        self.assertEqual('Current nebula guidance.', server.project_list()['projects'][0]['summary'])
        self.assertEqual([], server.project_find('obsolete')['matches'])

    def test_link_target_is_authoritative(self):
        target = self.repo/'projects/alpha/nested/current.md'
        target.parent.mkdir()
        target.write_text('Linked pulsar guidance.\n')
        self.index.write_text('- `alpha`: [label](projects/alpha/nested/current.md)\n')
        server = self.server()
        self.assertEqual('Linked pulsar guidance.', server.project_list()['projects'][0]['summary'])
        self.assertIn('Linked pulsar guidance.', server.project_brief('alpha')['text'])
        self.assertNotIn('active quasar', server.project_brief('alpha')['text'])
        self.assertIn('Linked pulsar guidance.', server.project_brief('alpha', include_history=True)['text'])
        self.assertEqual('projects/alpha/nested/current.md', server.project_brief('alpha')['segments'][1]['path'])

    def test_link_target_update(self):
        target = self.repo/'projects/alpha/current.md'
        target.write_text('Linked pulsar guidance.\n')
        original = (self.repo/'projects/alpha/brief.md').read_bytes()
        self.index.write_text('- `alpha`: [label](projects/alpha/current.md)\n')
        self.commit()
        origin = self.root/'origin.git'
        k.git(None, 'init', '--bare', '--initial-branch=main', str(origin))
        k.git(self.repo, 'remote', 'add', 'origin', str(origin))
        k.git(self.repo, 'push', 'origin', 'main')
        server = self.server()
        result = server.project_update('alpha', text='Additional linked guidance.',
                                      expectedRevision=server.snapshot()['revision'], op='add')
        self.assertTrue(result['pushed'])
        untouched = k.parse_document((self.repo/'projects/alpha/brief.md').read_text())
        self.assertEqual([original.decode()], [x['text'] for x in untouched['current']])
        self.assertIn('Additional linked guidance.', target.read_text())
        self.assertIn('Additional linked guidance.', server.project_brief('alpha')['text'])

    def test_symlink_target(self):
        (self.repo/'projects/alpha/link.md').symlink_to('brief.md')
        self.invalid('- `alpha`: [link](projects/alpha/link.md)')

    def test_link_with_active_status(self):
        self.index.write_text('- `alpha` (active): [brief](projects/alpha/brief.md)\n')
        self.assertEqual('| **Alpha** | synthetic | active quasar |',
                         self.server().project_list()['projects'][0]['summary'])

    def test_escape_link(self):
        self.invalid('- `alpha`: [escape](projects/alpha/../../rules/global.md)')

    def test_other_project_link(self):
        self.invalid('- `alpha`: [escape](projects/empty/brief.md)')

    def test_absolute_link(self):
        self.invalid('- `alpha`: [escape](/projects/alpha/brief.md)')

    def test_missing_link_target(self):
        self.invalid('- `alpha`: [missing](projects/alpha/missing.md)')

    def test_unknown_row(self):
        self.invalid('- `alpha` unsupported row format')

    def test_unknown_bullet(self):
        self.invalid('- unsupported row format')

    def test_unknown_unmapped_row(self):
        row = '- unsupported row format'
        self.index.write_text('# Synthetic INDEX\n## Unmapped titles\n'+row+'\n')
        with self.assertRaises(k.KnowledgeError) as cm:
            self.server()
        self.assertEqual('LAYOUT_INVALID', cm.exception.code)
        self.assertIn(row, str(cm.exception))

    def test_explicit_archive(self):
        self.index.write_text('- `alpha` (archive): [brief](projects/alpha/brief.md)\n')
        self.assertEqual([], self.server().project_list()['projects'])

    def test_archive_path(self):
        self.index.write_text('- `alpha`: [old](archive/alpha.md)\n')
        self.assertEqual([], self.server().project_list()['projects'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
