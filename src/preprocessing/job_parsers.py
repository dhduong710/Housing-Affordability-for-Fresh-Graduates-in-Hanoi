"""Conservative, source-aware rules for raw job cards; no title-based eligibility."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

POLICY_VERSION = 'jobs-0-24-months-month-default-v2'
EXPERIENCE_LIMIT = Decimal(24)


def folded(text):
    value = unicodedata.normalize('NFKC', str(text or '')).lower()
    value = ''.join(c for c in unicodedata.normalize('NFD', value)
                    if unicodedata.category(c) != 'Mn').replace('đ', 'd')
    value = re.sub('[–—−]', '-', value)
    return re.sub(r'\s+', ' ', value).strip()


def number(text):
    """Recognize decimal comma/dot and grouped thousands, rejecting malformed numbers."""
    value = text.strip()
    if re.fullmatch(r'\d{1,3}(?:[,.]\d{3})+', value):
        value = value.replace(',', '').replace('.', '')
    elif ',' in value and '.' in value:
        separator = ',' if value.rfind(',') > value.rfind('.') else '.'
        grouping = '.' if separator == ',' else ','
        whole, fraction = value.rsplit(separator, 1)
        if not re.fullmatch(r'\d{1,3}(?:' + re.escape(grouping) + r'\d{3})+', whole):
            raise ValueError('Invalid number grouping')
        value = whole.replace(grouping, '') + '.' + fraction
    elif value.count(',') + value.count('.') <= 1:
        value = value.replace(',', '.')
    else:
        raise ValueError('Invalid number')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid number') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('Invalid nonnegative number')
    return result


# Recognize names only. No mapping between old/new administrative boundaries or wards.
PROVINCES = [
    'An Giang', 'Bà Rịa - Vũng Tàu', 'Bắc Giang', 'Bắc Kạn', 'Bạc Liêu',
    'Bắc Ninh', 'Bến Tre', 'Bình Định', 'Bình Dương', 'Bình Phước', 'Bình Thuận',
    'Cà Mau', 'Cần Thơ', 'Cao Bằng', 'Đà Nẵng', 'Đắk Lắk', 'Đắk Nông',
    'Điện Biên', 'Đồng Nai', 'Đồng Tháp', 'Gia Lai', 'Hà Giang', 'Hà Nam',
    'Hà Tĩnh', 'Hải Dương', 'Hải Phòng', 'Hậu Giang', 'Hòa Bình', 'Hồ Chí Minh',
    'Huế', 'Hưng Yên', 'Khánh Hòa', 'Kiên Giang', 'Kon Tum', 'Lai Châu',
    'Lâm Đồng', 'Lạng Sơn', 'Lào Cai', 'Long An', 'Nam Định', 'Nghệ An',
    'Ninh Bình', 'Ninh Thuận', 'Phú Thọ', 'Phú Yên', 'Quảng Bình', 'Quảng Nam',
    'Quảng Ngãi', 'Quảng Ninh', 'Quảng Trị', 'Sóc Trăng', 'Sơn La', 'Tây Ninh',
    'Thái Bình', 'Thái Nguyên', 'Thanh Hóa', 'Thừa Thiên Huế', 'Tiền Giang',
    'Trà Vinh', 'Tuyên Quang', 'Vĩnh Long', 'Vĩnh Phúc', 'Yên Bái',
]


def parse_location(text):
    raw = re.sub(r'\s+', ' ', str(text or '')).strip()
    normalized = re.sub(r'\s*\(mới\)', '', raw, flags=re.I)
    normalized = re.sub(r'\b(?:hà\s+nội|ha\s+noi|hanoi|hn)\b', 'Hà Nội', normalized, flags=re.I)
    value = folded(normalized)
    hanoi = bool(re.search(r'\b(?:ha noi|hanoi|hn)\b', value))
    hidden = bool(re.search(r'\b\d+\s*(?:noi|dia diem)\s+khac\b', value)
                  or re.search(r'\.\.\.|…|\+\s*\d+', value))
    broad = bool(re.search(r'\b(?:toan quoc|mien bac|mien nam|mien trung|remote|tu xa)\b', value))
    places = {p for p in PROVINCES if re.search(r'\b' + re.escape(folded(p)) + r'\b', value)}
    if 'Thừa Thiên Huế' in places:
        places.discard('Huế')
    if re.search(r'\b(?:tp\.?\s*hcm|tphcm|hcm|sai gon)\b', value):
        places.add('Hồ Chí Minh')
    if hanoi:
        is_hanoi = True
    elif not value or hidden or broad or not places:
        is_hanoi = None
    else:
        is_hanoi = False
    multi = bool(hidden or len(places) + int(hanoi) > 1 or re.search(r'[&;|/]', value))
    return {'location_normalized': normalized, 'is_hanoi': is_hanoi,
            'is_multi_location': multi if value else None}


EXPERIENCE_ATOM = r'(\d+(?:[.,]\d+)?)\s*(thang|nam|months?|years?)?'


def parse_experience(text):
    value = folded(text)
    unknown = {'experience_min_months': None, 'experience_max_months': None,
               'is_entry_level': None}
    if re.search(r'\b(?:khong yeu cau|khong can)\b', value):
        if re.search(r'\d', value):
            return unknown
        return {**unknown, 'experience_min_months': 0, 'is_entry_level': True}
    value = re.sub(r'\b(?:kinh nghiem(?: chuyen mon)?|yeu cau)\b', '', value).strip()
    value = re.sub(r'\s+', ' ', value)
    lower, upper, lower_inclusive, upper_inclusive = None, None, True, True
    try:
        match = re.fullmatch(r'(?:tu\s+)?' + EXPERIENCE_ATOM + r'\s*(?:-|den|toi)\s*' + EXPERIENCE_ATOM, value)
        if match:
            first, unit1, second, unit2 = match.groups()
            unit1, unit2 = unit1 or unit2, unit2 or unit1
            if not unit1 or not unit2:
                return unknown
            lower = number(first) * (12 if unit1 in {'nam', 'year', 'years'} else 1)
            upper = number(second) * (12 if unit2 in {'nam', 'year', 'years'} else 1)
        else:
            match = re.fullmatch(r'(?:(tu|tren|hon|duoi|toi da|toi thieu|it nhat|toi|den)\s+)?'
                                 + EXPERIENCE_ATOM + r'\s*(tro len|tro xuong|\+)?', value)
            if not match or not match[3]:
                return unknown
            prefix, amount, unit, suffix = match.groups()
            lower_prefixes = {'tu', 'toi thieu', 'it nhat', 'tren', 'hon'}
            upper_prefixes = {'duoi', 'toi', 'toi da', 'den'}
            if prefix and suffix and not (
                    prefix in lower_prefixes and suffix in {'tro len', '+'}
                    or prefix in upper_prefixes and suffix == 'tro xuong'):
                return unknown
            amount = number(amount) * (12 if unit in {'nam', 'year', 'years'} else 1)
            if prefix in {'tu', 'toi thieu', 'it nhat', 'tren', 'hon'} or suffix in {'tro len', '+'}:
                lower = amount
                lower_inclusive = prefix not in {'tren', 'hon'}
            elif prefix in {'duoi', 'toi', 'toi da', 'den'} or suffix == 'tro xuong':
                lower, upper = Decimal(0), amount
                upper_inclusive = prefix != 'duoi'
            else:
                lower = upper = amount
    except ValueError:
        return unknown
    if upper is not None and (upper < lower or upper == lower and not (lower_inclusive and upper_inclusive)):
        return unknown
    eligible = lower < EXPERIENCE_LIMIT or lower == EXPERIENCE_LIMIT and lower_inclusive
    return {'experience_min_months': lower, 'experience_max_months': upper,
            'is_entry_level': eligible}


MONEY_ATOM = r'(\d+(?:[.,]\d+)*)\s*(trieu|tr|nghin|ngan|k|ty)?'
SCALES = {'trieu': 1_000_000, 'tr': 1_000_000, 'nghin': 1000,
          'ngan': 1000, 'k': 1000, 'ty': 1_000_000_000}


def parse_salary(text, source=''):
    value = folded(text)
    original = value
    result = dict.fromkeys(['salary_min', 'salary_max', 'salary_currency', 'salary_period'])
    periods = set()
    for period, pattern in [('month', r'\b(?:thang|months?|monthly)\b'),
                            ('year', r'\b(?:nam|years?|yearly|annually)\b'),
                            ('day', r'\b(?:ngay|days?|daily)\b'),
                            ('hour', r'\b(?:gio|hours?|hourly)\b')]:
        if re.search(pattern, value):
            periods.add(period)
        value = re.sub(pattern, '', value)
    if len(periods) == 1:
        result['salary_period'] = periods.pop()
    elif len(periods) > 1:
        result['salary_period'] = None
    else:
        result['salary_period'] = 'month'
    if not original:
        return result
    if re.search(r'\bthoa thuan\b|\bnegotiable\b', value):
        return result
    if value.strip(' /') == 'khong luong':
        return result
    currencies = set()
    for currency, pattern in [('USD', r'(?<![a-z])usd\b|us\$|\$'),
                              ('VND', r'(?<![a-z])(?:vnd|dong|d)\b|₫'),
                              ('EUR', r'(?<![a-z])eur\b|€')]:
        if re.search(pattern, value):
            currencies.add(currency)
        value = re.sub(pattern, '', value)
    if len(currencies) > 1:
        return result
    result['salary_currency'] = next(iter(currencies), None)
    value = re.sub(r'\b(?:moi|per|a|luong|muc luong)\b|/', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip()
    match = re.fullmatch(r'(?:tu\s+)?' + MONEY_ATOM + r'\s*(?:-|den|toi)\s*' + MONEY_ATOM, value)
    direction = None
    if match:
        first, scale1, second, scale2 = match.groups()
        scale1, scale2 = scale1 or scale2, scale2 or scale1
    else:
        match = re.fullmatch(r'(?:(tu|toi|den|tren|hon|duoi|up to)\s+)?' + MONEY_ATOM, value)
        if not match:
            return result
        direction, first, scale1 = match.groups()
        second, scale2 = first, scale1
    if result['salary_currency'] is None and folded(source) == 'topcv' and (scale1 or scale2):
        result['salary_currency'] = 'VND'
    if result['salary_currency'] is None:
        return result
    try:
        low = number(first) * SCALES.get(scale1, 1)
        high = number(second) * SCALES.get(scale2, 1)
    except ValueError:
        return result
    if low <= 0 or high <= 0 or low > high:
        return result
    if direction in {'tu', 'tren', 'hon'}:
        high = None
    elif direction in {'toi', 'den', 'duoi', 'up to'}:
        low = None
    result.update(salary_min=low, salary_max=high)
    return result
