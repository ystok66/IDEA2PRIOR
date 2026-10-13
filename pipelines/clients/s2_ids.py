"""Provider IDs at the HTTP boundary, independent of registry aliases."""
import re
from urllib.parse import quote, unquote


def paper_id_path(value):
    value = unquote(str(value)).strip()
    if re.fullmatch(r'(?:https?://openalex.org/)?W\d+', value, re.I):
        raise ValueError('OpenAlex IDs cannot be sent to the Semantic Scholar paper API')
    value = re.sub(r'^https?://(?:dx\.)?doi\.org/', 'DOI:', value, flags=re.I)
    if re.match(r'^10\.\d+/', value):
        value = 'DOI:' + value
    elif re.match(r'^\d{4}\.\d{4,5}(?:v\d+)?$', value):
        value = 'ARXIV:' + value
    if value.upper().startswith('ARXIV:'):
        value = re.sub(r'v\d+$', '', value, flags=re.I)
    # Keep documented DOI path separators; escape query/fragment delimiters.
    return quote(value, safe=':/')
