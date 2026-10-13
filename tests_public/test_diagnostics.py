import json
import unittest
from types import SimpleNamespace
from paperarena.diagnostics import safe_error, diagnostic_for_type


class DiagnosticTests(unittest.TestCase):
    def test_http_causes_keep_status_and_stage_without_provider_text(self):
        for status, code in [(401,'AUTHENTICATION_REJECTED'),(403,'PERMISSION_DENIED'),(404,'MODEL_UNAVAILABLE'),(429,'RATE_LIMITED'),(500,'PROVIDER_FAILURE'),(504,'PROVIDER_TIMEOUT')]:
            error = RuntimeError('test-key-example-private https://private.invalid raw body')
            error.response = SimpleNamespace(status_code=status)
            result = safe_error(error, 'seed_recall')
            self.assertEqual(result['code'], code)
            self.assertEqual(result['http_status'],status)
            self.assertEqual(result['phase'],'seed_recall')
            self.assertNotIn('private',json.dumps(result))

    def test_route_marker_reports_known_cause_only(self):
        result = safe_error(RuntimeError('get_channel_failed test-key-example-private'))
        self.assertEqual(result['code'],'PROVIDER_ROUTE_UNAVAILABLE')
        self.assertNotIn('test-key-example-private',str(result))

    def test_labels_cannot_carry_secrets_or_paths(self):
        result = diagnostic_for_type('https://private.invalid', phase='unsafe secret')
        self.assertEqual(result['type'],'RuntimeError')
        self.assertEqual(result['phase'],'unknown_stage')

    def test_local_failures_are_explained(self):
        self.assertEqual(safe_error(ModuleNotFoundError('test-key-example-private'))['code'],'RUNTIME_DEPENDENCY_MISSING')
        self.assertEqual(safe_error(PermissionError('test-key-example-private'))['code'],'LOCAL_FILE_ERROR')

    def test_missing_profile_key_is_specific(self):
        self.assertEqual(safe_error(ValueError('Missing API key for a routed API profile'))['code'],'MISSING_API_KEY')

    def test_wrapped_billing_error_retains_actual_provider_cause(self):
        error = RuntimeError('GeminiException {"error":{"code":402,"message":"Your prepayment credits are depleted. test-key-example-private"}}')
        error.status_code = 500
        result = safe_error(error, 'seed_recall')
        self.assertEqual(result['code'],'BILLING_CREDITS_EXHAUSTED')
        self.assertEqual(result['http_status'],402)
        self.assertEqual(result['adapter_http_status'],500)
        self.assertNotIn('test-key-example-private',str(result))

    def test_channel_failure_is_not_reported_as_invalid_parameters(self):
        error = RuntimeError('no available channel after excluding failed channels')
        error.status_code = 400
        self.assertEqual(safe_error(error)['code'],'PROVIDER_ROUTE_UNAVAILABLE')

if __name__ == '__main__': unittest.main()
