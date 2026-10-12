"""Use the existing visual shell; only global mode replaces its legacy driver."""
import re

def render(source):
    text=source.decode('utf-8')
    # Preserve the JSON theme manifest and the two canonical theme controllers.
    # No legacy fetch, launcher, demo, or telemetry event loop runs in this mode.
    text=re.sub(r'<script\b([^>]*)>.*?</script>',lambda m:m.group(0) if (
        'application/json' in m[1] or re.search(r'src=["\'](?:theme_core|theme_controller|network_node)\.js["\']',m[1])) else '',text,flags=re.S)
    text=re.sub(r'\s+on(?:click|change|input|keydown)="[^"]*"','',text)
    return text.replace('</head>','<link rel="stylesheet" href="global_ui.css"></head>').replace('</body>',
        '<script src="mail_flow.js"></script><script src="global_ui.js"></script></body>').encode()
