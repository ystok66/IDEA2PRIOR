import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipelines.clients import scholarly
from pipelines.tasks import prior_art_retrieval as retrieval


class RetrievalControlsTests(unittest.TestCase):
    def test_disabled_search_makes_no_http_request(self):
        runtime = SimpleNamespace(retrieval_policy={'enable_arxiv_search': False})
        with patch.object(scholarly, 'get_runtime', return_value=runtime), \
                patch.object(scholarly, 'outcome') as outcome, patch.object(scholarly, 'get') as get:
            self.assertEqual(scholarly.search_arxiv('a title'), [])
            get.assert_not_called()
            self.assertEqual(outcome.call_args.args, ('arxiv_search', 'SKIPPED'))

    def test_disabled_search_keeps_exact_id_lookup(self):
        runtime = SimpleNamespace(retrieval_policy={'enable_arxiv_search': False})
        response = Mock(text='<feed xmlns="http://www.w3.org/2005/Atom"/>')
        with patch.object(scholarly, 'get_runtime', return_value=runtime), \
                patch.object(scholarly, 'outcome'), patch.object(scholarly, 'get', return_value=response) as get:
            scholarly.search_arxiv('', id_list=['1802.04687'])
            self.assertEqual(get.call_args.kwargs['params']['id_list'], '1802.04687')
            self.assertNotIn('search_query', get.call_args.kwargs['params'])

    def test_expert_timeout_is_configurable_and_disables_sdk_retries(self):
        for timeout in [120, 35]:
            runtime = SimpleNamespace(retrieval_policy={'expert_retrieval_timeout_seconds': timeout})
            with patch.object(retrieval, 'get_runtime', return_value=runtime), \
                    patch.object(retrieval, 'invoke_model') as invoke:
                retrieval.invoke_expert_retrieval(Mock(), [{'role': 'user', 'content': 'synthetic'}])
                self.assertEqual(invoke.call_args.kwargs['request_parameters'], {'timeout': timeout, 'num_retries': 0})

    def test_expert_timeout_propagates_without_retry_or_fallback(self):
        with patch.object(retrieval, 'get_runtime', return_value=None), \
                patch.object(retrieval, 'invoke_model', side_effect=TimeoutError) as invoke:
            with self.assertRaises(TimeoutError):
                retrieval.invoke_expert_retrieval(Mock(), [])
            self.assertEqual(invoke.call_count, 1)
