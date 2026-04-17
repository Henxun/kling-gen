'''API helper tests.'''

import base64
import tempfile
import unittest
from pathlib import Path

from api import encode_image_to_base64, resolve_image_value


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


if __name__ == '__main__':
    unittest.main()
