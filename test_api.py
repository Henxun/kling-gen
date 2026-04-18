'''API helper tests.'''

import base64
import tempfile
import unittest
from pathlib import Path

from api import (
    KlingAIClient,
    encode_image_to_base64,
    normalize_element_list_input,
    normalize_image_list_input,
    resolve_image_value,
)


class ApiImageEncodingTests(unittest.TestCase):
    '''Validate image input formatting rules for API requests.'''

    def test_encode_image_to_base64_without_data_prefix(self) -> None:
        '''Local files must be encoded as plain base64 text.'''

        with tempfile.NamedTemporaryFile('wb', suffix='.png', delete=False) as image_file:
            image_file.write(b'\x89PNG\r\n\x1a\nunit-test-data')
            file_path = image_file.name

        try:
            encoded_value = encode_image_to_base64(file_path)
            self.assertNotIn('data:image', encoded_value)
            self.assertEqual(
                encoded_value,
                base64.b64encode(Path(file_path).read_bytes()).decode('utf-8'),
            )
        finally:
            Path(file_path).unlink(missing_ok=True)

    def test_resolve_image_value_keeps_url_unchanged(self) -> None:
        '''Remote URLs should be passed through directly.'''

        image_url = 'https://example.com/test.png'
        self.assertEqual(resolve_image_value(image_url), image_url)

    def test_normalize_image_list_supports_json_and_fallback(self) -> None:
        '''image_list should accept JSON text and fallback to image_path when empty.'''

        image_list = normalize_image_list_input('[{"image":"https://example.com/a.png"}]')
        self.assertEqual(image_list, [{'image': 'https://example.com/a.png'}])

        fallback_list = normalize_image_list_input('', fallback_image_path='https://example.com/fallback.png')
        self.assertEqual(fallback_list, [{'image': 'https://example.com/fallback.png'}])

    def test_normalize_element_list_supports_json_and_raw_ids(self) -> None:
        '''element_list should accept JSON objects and scalar IDs.'''

        from_json = normalize_element_list_input('[{"element_id":829836802793406551}]')
        self.assertEqual(from_json, [{'element_id': 829836802793406551}])

        from_scalar = normalize_element_list_input([829836802793406551, '123'])
        self.assertEqual(from_scalar, [{'element_id': 829836802793406551}, {'element_id': 123}])


class ApiModeConfigTests(unittest.TestCase):
    '''Validate mode-specific path/status config resolution.'''

    def test_omni_mode_uses_task_status_and_task_result_paths(self) -> None:
        '''omni_image mode should use its own status/result/error paths.'''

        client = KlingAIClient(
            {
                'auth_mode': 'api_key',
                'api_key': 'test-token',
                'base_url': 'https://api-beijing.klingai.com',
                'api_mode': 'omni_image',
                'output_dir': 'outputs',
                'headers': {
                    'Authorization': '{authorization}',
                    'Content-Type': 'application/json',
                },
                'request_templates': {'omni_image': {'prompt': '{prompt}'}},
                'status_paths': {'omni_image': 'data.task_status'},
                'result_url_paths': {'omni_image': 'data.task_result.images.0.url'},
                'error_message_paths': {'omni_image': 'data.task_status_msg'},
                'success_status_values_by_mode': {'omni_image': ['succeed']},
                'running_status_values_by_mode': {'omni_image': ['submitted', 'processing']},
                'failed_status_values_by_mode': {'omni_image': ['failed']},
            }
        )

        self.assertEqual(client.status_path, 'data.task_status')
        self.assertEqual(client.result_url_path, 'data.task_result.images.0.url')
        self.assertEqual(client.error_message_path, 'data.task_status_msg')
        self.assertEqual(client.success_status_values, {'succeed'})
        self.assertEqual(client.running_status_values, {'submitted', 'processing'})
        self.assertEqual(client.failed_status_values, {'failed'})

    def test_generations_mode_uses_task_status_and_task_result_paths(self) -> None:
        '''generations mode should use task_status/task_result mappings.'''

        client = KlingAIClient(
            {
                'auth_mode': 'api_key',
                'api_key': 'test-token',
                'base_url': 'https://api-beijing.klingai.com',
                'api_mode': 'generations',
                'output_dir': 'outputs',
                'headers': {
                    'Authorization': '{authorization}',
                    'Content-Type': 'application/json',
                },
                'request_templates': {'generations': {'prompt': '{prompt}'}},
                'status_paths': {'generations': 'data.task_status'},
                'result_url_paths': {'generations': 'data.task_result.images.0.url'},
                'error_message_paths': {'generations': 'data.task_status_msg'},
                'success_status_values_by_mode': {'generations': ['succeed']},
                'running_status_values_by_mode': {'generations': ['submitted', 'processing']},
                'failed_status_values_by_mode': {'generations': ['failed']},
            }
        )

        self.assertEqual(client.status_path, 'data.task_status')
        self.assertEqual(client.result_url_path, 'data.task_result.images.0.url')
        self.assertEqual(client.error_message_path, 'data.task_status_msg')
        self.assertEqual(client.success_status_values, {'succeed'})
        self.assertEqual(client.running_status_values, {'submitted', 'processing'})
        self.assertEqual(client.failed_status_values, {'failed'})

    def test_run_task_uses_series_images_fallback_path(self) -> None:
        '''When task_result.images is empty, fallback path should be used.'''

        class FakeClient(KlingAIClient):
            def create_task(self, task_data: dict[str, str]) -> tuple[str, dict]:  # type: ignore[override]
                return 'task-1', {'data': {'task_id': 'task-1'}}

            def wait_for_result(self, task_id: str) -> dict:  # type: ignore[override]
                return {
                    'data': {
                        'task_status': 'succeed',
                        'task_result': {
                            'series_images': [{'url': 'https://example.com/fallback.png'}],
                        },
                    }
                }

            def download_result(self, result_url: str, output_name: str) -> Path:  # type: ignore[override]
                self._downloaded_url = result_url
                return Path('outputs/fallback.png')

        client = FakeClient(
            {
                'auth_mode': 'api_key',
                'api_key': 'test-token',
                'base_url': 'https://api-beijing.klingai.com',
                'api_mode': 'omni_image',
                'output_dir': 'outputs',
                'headers': {
                    'Authorization': '{authorization}',
                    'Content-Type': 'application/json',
                },
                'request_templates': {'omni_image': {'prompt': '{prompt}'}},
                'result_url_paths': {'omni_image': 'data.task_result.images.0.url'},
                'result_url_fallback_paths_by_mode': {'omni_image': ['data.task_result.series_images.0.url']},
            }
        )

        result = client.run_task({'prompt': 'test', 'output_name': 'demo'})
        self.assertEqual(result['result_url'], 'https://example.com/fallback.png')


if __name__ == '__main__':
    unittest.main()
