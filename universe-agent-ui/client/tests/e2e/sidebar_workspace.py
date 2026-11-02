"""
E2E: Sidebar projects + chats (W6) — the workspace navigator, live.

Seeds a real chat via the bridge API (DB row), then drives the UI:
  - projects list renders with General + goal counts; create a project
    via the New project form; switch projects switches chats
  - chats of the active project: dot, title, relative time, ⋯ menu
  - menu actions through the real endpoints: Rename, Pin (order change),
    Branch (⑂ tag appears), Archive (moves to Archived section),
    Unarchive, hard Delete (confirm dialog)
  - delete project: its chats fall back to General (general protected)

Run: python client/tests/e2e/sidebar_workspace.py  (bridge + vite up)
"""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:5199'
BRIDGE = 'http://127.0.0.1:8738'


def bridge(path: str, method: str = 'GET', body: dict | None = None) -> dict:
    req = urllib.request.Request(f'{BRIDGE}{path}', method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={'Content-Type': 'application/json'} if body else {})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def seed_goal(project: str, text: str, title: str) -> str:
    """Insert a finished goal row directly (the bridge's own start path
    needs a provider + run loop; the DB row is the same thing at rest)."""
    # via branch-less direct insert through the branch endpoint of a
    # staging goal is overkill — use the real API the sidebar reads.
    # goals are created by POST /api/turn, but without a provider it 422s.
    # The honest seed path: insert with sqlite (the same store the bridge
    # reads) — mirroring what a finished goal looks like at rest.
    import sqlite3, os, time
    db = os.path.expanduser('~/.universe/universe.db')
    gid = f'goal-w6-{int(time.time()*1000)%100000}-{os.urandom(3).hex()}'
    con = sqlite3.connect(db)
    con.execute(
        'INSERT INTO goals(id,text,status,created_at,completed_at,project_id,'
        'title,archived,deleted,pinned) VALUES(?,?,?,?,?,?,?,?,?,?);',
        (gid, text, 'completed', time.time() - 3600, time.time() - 3590,
         project, title, 0, 0, 0))
    con.commit()
    con.close()
    return gid


def main() -> int:
    failures = []

    def check(name: str, ok: bool, detail: str = '') -> None:
        print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  [{detail}]' if detail else ''))
        if not ok:
            failures.append(name)

    # startup cleanup: leftovers from a crashed run must not leak in
    import sqlite3, os
    con = sqlite3.connect(os.path.expanduser('~/.universe/universe.db'))
    con.execute("DELETE FROM goals WHERE id LIKE 'goal-w6-%' OR title LIKE '%renamed report%' OR text='write the report';")
    con.execute("DELETE FROM projects WHERE id LIKE 'proj-%' AND name LIKE 'W6%';")
    con.commit(); con.close()

    # seed two chats in General
    g1 = seed_goal('general', 'research pdf libraries', 'pdf research')
    g2 = seed_goal('general', 'write the report', 'report draft')

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        # one dialog handler for everything: prompts answer from the
        # expected_next buffer, confirms just accept
        expected = {'next': None}
        def on_dialog(d):
            if d.type == 'prompt':
                d.accept(expected['next'] or '')
            else:
                d.accept()
        page.on('dialog', on_dialog)
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)[:150]))

        page.goto(BASE, wait_until='domcontentloaded')
        page.wait_for_selector('.proj-list', timeout=25000)

        # --- projects render
        check('General project in list', 'General' in page.locator('.proj-list').inner_text())

        # --- chats render with title + relative time
        page.wait_for_selector('.task-item', timeout=10000)
        rows = page.locator('.task-item')
        titles = page.locator('.t-title').all_inner_texts()
        check('seeded chats visible', 'pdf research' in titles and 'report draft' in titles, str(titles))
        check('relative time shown', any(t in ('now', '1h') for t in page.locator('.t-time').all_inner_texts()))

        # --- create a project through the form
        page.locator('button[aria-label="New project"]').click()
        page.locator('input[aria-label="New project name"]').fill('W6 Lab')
        page.keyboard.press('Enter')
        page.wait_for_timeout(800)
        check('project created', 'W6 Lab' in page.locator('.proj-list').inner_text())

        # the chats label now names the active project (CSS uppercases)
        chats_label = page.locator('.side-label', has_text='Chats ·').inner_text()
        check('chats label follows project', 'w6 lab' in chats_label.lower(), chats_label)

        # --- rename the project via its menu
        proj_row = page.locator('.proj-item', has_text='W6 Lab').first
        proj_row.locator('.proj-menu-btn').hover()
        proj_row.locator('.proj-menu-btn').click()
        page.wait_for_selector('.chat-menu.proj', timeout=5000)
        expected['next'] = 'W6 Renamed'
        page.locator('.chat-menu.proj button:has-text("Rename")').click()
        page.wait_for_timeout(800)
        check('project renamed', 'W6 Renamed' in page.locator('.proj-list').inner_text())

        # --- switch to General and exercise the chat menu
        page.locator('.pi-btn', has_text='General').click()
        page.wait_for_timeout(600)
        row1 = page.locator('.task-item', has_text='pdf research')
        row1.hover()
        row1.locator('.tm-btn').click()
        page.wait_for_selector('.chat-menu', timeout=5000)
        menu_text = page.locator('.chat-menu').inner_text()
        check('chat menu offers lifecycle actions',
              all(w in menu_text for w in ('Rename', 'Branch from here', 'Pin', 'Archive', 'Delete forever')),
              menu_text.replace('\n', ' | '))

        # pin: moves to front + pin icon
        page.locator('.chat-menu button:has-text("Pin")').click()
        page.wait_for_timeout(700)
        first_title = page.locator('.task-item .t-title').first.inner_text()
        check('pin reorders chat to top', first_title == 'pdf research', first_title)

        # rename via menu
        row2 = page.locator('.task-item', has_text='report draft')
        row2.hover()
        row2.locator('.tm-btn').click()
        expected['next'] = 'renamed report'
        page.locator('.chat-menu button:has-text("Rename")').click()
        page.wait_for_timeout(700)
        check('chat renamed', 'renamed report' in page.locator('.task-list').inner_text())

        # branch: new chat with ⑂ tag + ⑂ title
        row3 = page.get_by_role('listitem', name='Chat completed: renamed report')
        row3.hover()
        row3.locator('.tm-btn').click()
        page.locator('.chat-menu button:has-text("Branch")').click()
        page.wait_for_timeout(700)
        check('branch creates tagged chat',
              page.locator('.t-branch').count() >= 1 and
              '⑂' in page.locator('.task-list').inner_text())

        # archive: leaves active chats, lands in Archived (the ORIGINAL chat,
        # not its ⑂ branch copy — disambiguate by the exact aria-label)
        row4 = page.get_by_role('listitem', name='Chat completed: renamed report')
        row4.hover()
        row4.locator('.tm-btn').click()
        page.locator('.chat-menu button:has-text("Archive")').click()
        page.wait_for_timeout(700)
        active_titles = page.locator('.task-list:not(.archived) .t-title').all_inner_texts()
        # the ORIGINAL must be gone; the ⑂ branch copy is a different title
        check('chat archived (out of active list)',
              not any(t == 'renamed report' for t in active_titles), str(active_titles))
        arch_btn = page.locator('.arch-toggle')
        check('archived section counts 1', '1' in arch_btn.inner_text(), arch_btn.inner_text())
        arch_btn.click()
        page.wait_for_timeout(400)
        check('archived chat restorable view', 'renamed report' in page.locator('.task-list.archived').inner_text())

        # unarchive from the archived row's menu (scoped to the archived list)
        arch_row = page.locator('.task-list.archived .task-item', has_text='renamed report').first
        arch_row.hover()
        arch_row.locator('.tm-btn').click()
        page.locator('.chat-menu button:has-text("Unarchive")').click()
        page.wait_for_timeout(700)
        active_titles2 = page.locator('.task-list:not(.archived) .t-title').all_inner_texts()
        check('unarchive restores chat', any(t == 'renamed report' for t in active_titles2))

        # hard delete (dialog auto-accepted)
        victim = page.get_by_role('listitem', name='Chat completed: renamed report')
        victim.hover()
        victim.locator('.tm-btn').click()
        page.locator('.chat-menu button:has-text("Delete forever")').click()
        page.wait_for_timeout(700)
        check('hard delete removes chat',
              not any(t == 'renamed report' for t in page.locator('.t-title').all_inner_texts()))

        # --- delete the project: falls back active to General
        w6row = page.locator('.proj-item', has_text='W6 Renamed').first
        w6row.locator('.proj-menu-btn').hover()
        w6row.locator('.proj-menu-btn').click()
        page.wait_for_selector('.chat-menu.proj', timeout=5000)
        page.locator('.chat-menu.proj button:has-text("Delete")').click()
        page.wait_for_timeout(800)
        proj_names = page.locator('.proj-list').inner_text()
        chats_lbl = page.locator('.side-label', has_text='Chats ·').inner_text()
        check('project deleted, falls back to General',
              'W6 Renamed' not in proj_names and 'general' in chats_lbl.lower(),
              f'projects={proj_names!r} label={chats_lbl!r}')

        # General has no project menu (protected)
        check('General protected (no menu)',
              page.locator('.proj-item', has_text='General').locator('.proj-menu-btn').count() == 0)

        check('zero page errors', len(errs) == 0, '; '.join(errs[:2]))
        b.close()

    # cleanup the seeds through the real endpoint
    for gid in (g1, g2):
        try:
            bridge(f'/api/turn/{gid}?hard=true', method='DELETE')
        except Exception:
            pass
    print('cleanup done')

    print(f'\n{len(failures)} failure(s)' if failures else '\nALL PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
