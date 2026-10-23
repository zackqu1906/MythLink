"""Use macOS's enabled static proxies for the standalone Codex child."""
import os
import sys
import urllib.request


def app_server_environment():
    env = os.environ.copy()
    # Explicit proxy settings (including empty values and NO_PROXY) take
    # precedence. Do not replace a caller's routing policy.
    explicit = {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
    if sys.platform != 'darwin' or any(key.lower() in explicit for key in env):
        return env
    try:
        proxies = urllib.request.getproxies()
        additions = {key.upper()+'_PROXY':proxies[key] for key in ('http', 'https')
                     if proxies.get(key)}
        if not additions:
            return env
        # urllib uses this same macOS provider for proxy_bypass. PAC-only
        # configurations are not converted into a guessed static proxy.
        from _scproxy import _get_proxy_settings
        settings = _get_proxy_settings()
        bypass = ['localhost', '127.0.0.1', '::1']
        bypass.extend(value for value in settings.get('exceptions', ())
                      if isinstance(value, str) and value != '<local>')
        additions['NO_PROXY'] = ','.join(dict.fromkeys(bypass))
    except (ImportError, OSError, ValueError):
        return env
    env.update(additions)
    return env
