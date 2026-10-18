"""Local UI screenshot fixture only: no authentication or business persistence."""
import argparse
import json
from http.server import SimpleHTTPRequestHandler, HTTPServer
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--role', choices=['Customer', 'Owner'], default='Customer')
parser.add_argument('--port', type=int, default=4175)
args = parser.parse_args()
dist = Path(__file__).resolve().parents[1] / 'dist'
if not (dist / 'index.html').exists():
    raise SystemExit('Build the frontend first: pnpm build')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(dist), **kw)

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/session':
            return self.reply({'user': {'displayName': 'Demo customer' if args.role == 'Customer' else 'Demo owner', 'role': args.role}, 'mode': 'mock', 'csrfToken': 'fixture-only'})
        if self.path == '/api/orders/mine':
            if args.role != 'Customer':
                return self.reply({}, 403)
            return self.reply({'orders': [{'id': 'DEMO-001', 'summary': '12 vanilla birthday cupcakes', 'status': 'Draft', 'requiredAt': '2026-10-25T03:00:00Z'}]})
        if self.path.startswith('/api/'):
            return self.reply({}, 404)
        super().do_GET()

    def do_POST(self):
        if self.path != '/api/order-requests':
            return self.reply({}, 404)
        if args.role != 'Customer' or self.headers.get('X-CSRF-Token') != 'fixture-only':
            return self.reply({}, 403)
        size = int(self.headers.get('Content-Length', '0'))
        if size > 20000:
            return self.reply({}, 422)
        try:
            body = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeDecodeError):
            return self.reply({}, 422)
        if not isinstance(body, dict) or not isinstance(body.get('message'), str) or not body['message'].strip():
            return self.reply({}, 422)
        return self.reply({
            'summary': {'items': [], 'required_at': None, 'collection_kind': 'at',
                        'allergen_concerns': None, 'budget': None},
            'issues': ['This visual preview cannot interpret or save orders. Use the BAKE-11 backend demo for the complete flow.'],
            'confirmationToken': None,
        })


print(f'UI TEST FIXTURE ONLY ({args.role}): http://127.0.0.1:{args.port}', flush=True)
HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
