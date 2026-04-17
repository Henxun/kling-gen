'''主界面辅助逻辑测试。'''

import tempfile
import unittest
from pathlib import Path

from main import build_output_name, parse_table_file


class MainHelperTests(unittest.TestCase):
    '''覆盖表格读取与输出命名的核心逻辑。'''

    def test_build_output_name_from_local_path(self) -> None:
        '''本地文件路径应生成“原名_ai”。'''

        self.assertEqual(build_output_name(r'C:\demo\cat.png'), 'cat_ai')

    def test_build_output_name_from_url(self) -> None:
        '''远程 URL 也应按文件名主体生成“原名_ai”。'''

        self.assertEqual(
            build_output_name('https://example.com/images/dog.webp'),
            'dog_ai',
        )

    def test_parse_table_file_supports_chinese_headers(self) -> None:
        '''中文表头文件应被正确解析。'''

        with tempfile.NamedTemporaryFile('w', encoding='utf-8-sig', newline='', suffix='.csv', delete=False) as csv_file:
            csv_file.write('图片路径,提示词\n')
            csv_file.write('C:/images/cat.png,一只可爱的猫\n')
            file_path = csv_file.name

        try:
            rows = parse_table_file(file_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['image_path'], 'C:/images/cat.png')
            self.assertEqual(rows[0]['prompt'], '一只可爱的猫')
        finally:
            Path(file_path).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
