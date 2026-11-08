"""Shared in-memory listing transport for flow tests; no live network."""
URL = 'https://www.topcv.vn/tim-viec-lam-moi-nhat'


NEXT = URL + '?page=2'


PROFILE = {'name': 'all_jobs', 'url': URL}


AT = '2026-11-03T08:00:00+00:00'


def html_page(entries, following=False):
    cards = ''.join(f'''<div class="job-item-search-result"><h3 class="title"><a href="/viec-lam/dev/{identifier}.html">Job {identifier}</a></h3>
        <a class="company">Fixture</a><label class="title-salary">Thỏa thuận</label><div class="label-content">
        <a class="address">Hồ Chí Minh</a><label class="exp">{experience}</label></div></div>''' for identifier, experience in entries)
    next_control = '<a rel="next" data-href="?page=2">Next</a>' if following else ''
    return '<div class="job-list-search-result">' + cards + '</div>' + next_control + f'<span id="job-listing-paginate-text"><span class="hight-light">{1 if following else 2}</span> / 2 trang</span>'


class FakeClient:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def load(self, task):
        self.calls.append(('list', task['page_number']))
        entries = [('1', '6 tháng'), ('2', '3 năm'), ('3', 'Không rõ')] if task['page_number'] == 1 else [('3', 'Không rõ'), ('4', '2 năm')]
        html = html_page(entries, task['page_number'] == 1)
        return html, AT



class UpdatedListings(FakeClient):
    def load(self, task):
        self.calls.append(('list', task['page_number']))
        entries = [('1', '6 tháng'), ('5', 'Không yêu cầu')] if task['page_number'] == 1 else [('3', 'Không rõ'), ('6', '1 năm')]
        return html_page(entries, task['page_number'] == 1), AT

