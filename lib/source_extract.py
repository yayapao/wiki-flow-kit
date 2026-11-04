"""Extract supported source text without a generation model."""
from html.parser import HTMLParser
import shutil
import subprocess
from pathlib import Path


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript', 'template'}:
            self.hidden.append(tag)
        if tag in {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'tr'} and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
        if tag in {'p', 'div', 'li', 'h1', 'h2', 'h3', 'tr'} and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_text(source, pdf_python=None):
    ext = source.suffix.lower()
    if ext == '.pdf':
        executable = shutil.which('pdftotext') or '/opt/homebrew/bin/pdftotext'
        command = [pdf_python, str(Path(__file__).with_name('pdf_extract.py')), str(source)] if pdf_python else [executable, '-layout', str(source), '-']
        try:
            r = subprocess.run(command, capture_output=True, timeout=120)
        except FileNotFoundError:
            raise RuntimeError('PDF 提取需要 Poppler pdftotext；也可使用客户端已有 PDF 读取工具') from None
        if r.returncode:
            raise RuntimeError('PDF 提取失败: ' + r.stderr.decode(errors='replace')[-1000:])
        text = r.stdout.decode('utf-8', errors='replace')
    elif ext in {'.html', '.htm'}:
        parser = VisibleHTML()
        parser.feed(source.read_text(encoding='utf-8', errors='replace'))
        text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
    elif ext in {'.md', '.mdx', '.txt', '.csv', '.org'}:
        text = source.read_text(encoding='utf-8-sig')
    else:
        raise ValueError('该格式需用客户端文件解析工具或 llm_wiki 导出文本；extract 支持 Markdown、文本、HTML 和文本型 PDF')
    if not text.strip():
        raise ValueError('未提取到正文；扫描 PDF 或图片需要客户端 OCR，不能当作已摄入')
    return text
