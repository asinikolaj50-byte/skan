#!/usr/bin/env python3
"""
OSINT Combo Bot — combines holehe (email OSINT) + user-scanner (username OSINT)
"""

import argparse
import asyncio
import re
import sys
import time

from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)

R = Fore.RED
G = Fore.GREEN
C = Fore.CYAN
Y = Fore.YELLOW
M = Fore.MAGENTA
W = Fore.WHITE
X = Style.RESET_ALL
B = Style.BRIGHT

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

BANNER = f"""
{B}{C}
  ██████╗ ███████╗██╗███╗   ██╗████████╗
 ██╔═══██╗██╔════╝██║████╗  ██║╚══██╔══╝
 ██║   ██║███████╗██║██╔██╗ ██║   ██║
 ██║   ██║╚════██║██║██║╚██╗██║   ██║
 ╚██████╔╝███████║██║██║ ╚████║   ██║
  ╚═════╝ ╚══════╝╚═╝╚═╝  ╚═══╝   ╚═╝{X}
{B}{M}   ██████╗ ██████╗ ███╗   ███╗██████╗  ██████╗ {X}
{B}{M}  ██╔════╝██╔═══██╗████╗ ████║██╔══██╗██╔═══██╗{X}
{B}{M}  ██║     ██║   ██║██╔████╔██║██████╔╝██║   ██║{X}
{B}{M}  ██║     ██║   ██║██║╚██╔╝██║██╔══██╗██║   ██║{X}
{B}{M}  ╚██████╗╚██████╔╝██║ ╚═╝ ██║██████╔╝╚██████╔╝{X}
{B}{M}   ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚═════╝  ╚═════╝ {X}

{Y}  Email OSINT (holehe) + Username Scanner combined{X}
{C}  github.com/megadose/holehe  |  user-scanner{X}
"""


def print_banner():
    print(BANNER)


def is_email(target: str) -> bool:
    return bool(EMAIL_RE.match(target))


# ─── HOLEHE (email mode) ───────────────────────────────────────────────────

async def run_holehe(email: str, only_found: bool = False, timeout: int = 10):
    import importlib
    import pkgutil
    import httpx
    import trio
    from holehe.instruments import TrioProgress

    def import_submodules(package, recursive=True):
        if isinstance(package, str):
            package = importlib.import_module(package)
        results = {}
        for loader, name, is_pkg in pkgutil.walk_packages(package.__path__):
            full_name = package.__name__ + '.' + name
            try:
                results[full_name] = importlib.import_module(full_name)
                if recursive and is_pkg:
                    results.update(import_submodules(full_name))
            except Exception:
                pass
        return results

    def get_functions(modules):
        websites = []
        for module in modules:
            if len(module.split(".")) > 3:
                modu = modules[module]
                site = module.split(".")[-1]
                if hasattr(modu, site) and callable(getattr(modu, site)):
                    websites.append(modu.__dict__[site])
        return websites

    domain_map = {
        'aboutme': 'about.me', 'adobe': 'adobe.com', 'amazon': 'amazon.com',
        'anydo': 'any.do', 'archive': 'archive.org', 'atlassian': 'atlassian.com',
        'bitmoji': 'bitmoji.com', 'blablacar': 'blablacar.com', 'bodybuilding': 'bodybuilding.com',
        'buymeacoffee': 'buymeacoffee.com', 'codecademy': 'codecademy.com',
        'codepen': 'codepen.io', 'coroflot': 'coroflot.com', 'deliveroo': 'deliveroo.com',
        'devrant': 'devrant.com', 'diigo': 'diigo.com', 'discord': 'discord.com',
        'docker': 'docker.com', 'ebay': 'ebay.com', 'ello': 'ello.co',
        'envato': 'envato.com', 'eventbrite': 'eventbrite.com', 'evernote': 'evernote.com',
        'fanpop': 'fanpop.com', 'firefox': 'firefox.com', 'flickr': 'flickr.com',
        'freelancer': 'freelancer.com', 'garmin': 'garmin.com', 'github': 'github.com',
        'google': 'google.com', 'gravatar': 'gravatar.com', 'imgur': 'imgur.com',
        'instagram': 'instagram.com', 'issuu': 'issuu.com', 'komoot': 'komoot.com',
        'laposte': 'laposte.fr', 'lastfm': 'last.fm', 'lastpass': 'lastpass.com',
        'mail_ru': 'mail.ru', 'myspace': 'myspace.com', 'naturabuy': 'naturabuy.fr',
        'nike': 'nike.com', 'odnoklassniki': 'ok.ru', 'office365': 'office365.com',
        'parler': 'parler.com', 'patreon': 'patreon.com', 'pinterest': 'pinterest.com',
        'plurk': 'plurk.com', 'pornhub': 'pornhub.com', 'protonmail': 'protonmail.ch',
        'quora': 'quora.com', 'rambler': 'rambler.ru', 'redtube': 'redtube.com',
        'replit': 'replit.com', 'rocketreach': 'rocketreach.co', 'samsung': 'samsung.com',
        'sevencups': '7cups.com', 'smule': 'smule.com', 'snapchat': 'snapchat.com',
        'soundcloud': 'soundcloud.com', 'sporcle': 'sporcle.com', 'spotify': 'spotify.com',
        'strava': 'strava.com', 'taringa': 'taringa.net', 'tumblr': 'tumblr.com',
        'twitter': 'twitter.com', 'venmo': 'venmo.com', 'vivino': 'vivino.com',
        'vsco': 'vsco.co', 'wattpad': 'wattpad.com', 'wordpress': 'wordpress.com',
        'xing': 'xing.com', 'yahoo': 'yahoo.com', 'hubspot': 'hubspot.com',
        'pipedrive': 'pipedrive.com', 'insightly': 'insightly.com', 'zoho': 'zoho.com',
        'axonaut': 'axonaut.com', 'amocrm': 'amocrm.com',
    }

    async def launch(module, email, client, out):
        name = str(module).split('<function ')[1].split(' ')[0] if '<function ' in str(module) else module.__name__
        domain = domain_map.get(name, f'{name}.com')
        try:
            await module(email, client, out)
        except Exception:
            out.append({
                "name": name, "domain": domain,
                "rateLimit": False, "error": True, "exists": False,
                "emailrecovery": None, "phoneNumber": None, "others": None
            })

    sys.path.insert(0, '.')
    modules = import_submodules("holehe.modules")
    websites = get_functions(modules)

    client = httpx.AsyncClient(timeout=timeout)
    out = []

    instrument = TrioProgress(len(websites))
    trio.lowlevel.add_instrument(instrument)
    async with trio.open_nursery() as nursery:
        for website in websites:
            nursery.start_soon(launch, website, email, client, out)
    trio.lowlevel.remove_instrument(instrument)

    out = sorted(out, key=lambda i: i.get('name', ''))
    await client.aclose()

    found = 0
    print(f"\n{B}{'='*50}{X}")
    print(f"{B}{C} Email OSINT results for: {email}{X}")
    print(f"{B}{'='*50}{X}")

    for r in out:
        if r.get("rateLimit"):
            if not only_found:
                print(f"  {Y}[~] {r['domain']} (rate limit){X}")
        elif r.get("error"):
            if not only_found:
                print(f"  {R}[!] {r['domain']} (error){X}")
        elif r.get("exists"):
            extra = ""
            if r.get("emailrecovery"):
                extra += f" — recovery: {r['emailrecovery']}"
            if r.get("phoneNumber"):
                extra += f" / phone: {r['phoneNumber']}"
            print(f"  {G}[+] {r['domain']}{extra}{X}")
            found += 1
        else:
            if not only_found:
                print(f"  {M}[-] {r['domain']}{X}")

    print(f"\n{C}[i] Email found on {G}{found}{C} sites out of {len(websites)} checked{X}")
    return out


# ─── USER-SCANNER (username mode) ─────────────────────────────────────────

def run_user_scanner(username: str, only_found: bool = False, verbose: bool = False,
                     category: str = None, no_nsfw: bool = False):
    sys.path.insert(0, '.')

    from user_scanner.core.helpers import (
        ScanConfig, load_categories, load_modules, get_site_name, find_category
    )
    from user_scanner.core.orchestrator import run_user_full, run_user_category
    from user_scanner.core.result import Status

    config = ScanConfig(
        allow_loud=False,
        only_found=only_found,
        no_nsfw=no_nsfw,
        verbose=verbose,
    )

    print(f"\n{B}{'='*50}{X}")
    print(f"{B}{C} Username scan results for: {username}{X}")
    print(f"{B}{'='*50}{X}")

    if category:
        cat_path = load_categories(is_email=False, no_nsfw=no_nsfw).get(category)
        if cat_path:
            results = run_user_category(cat_path, username, config)
        else:
            print(f"{R}[!] Category '{category}' not found{X}")
            results = []
    else:
        results = run_user_full(username, config)

    found = sum(1 for r in results if r.status == Status.TAKEN)
    print(f"\n{C}[i] Username found on {G}{found}{C} sites out of {len(results)} checked{X}")
    return results


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="osint-combo",
        description="OSINT Combo Bot — holehe (email) + user-scanner (username) in one tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py target@example.com          # auto-detect email → run holehe
  python main.py johndoe                     # auto-detect username → run user-scanner
  python main.py target@example.com --both   # run both holehe + user-scanner on email
  python main.py johndoe --only-found        # only show found results
  python main.py johndoe -c dev              # scan only dev category
  python main.py johndoe --no-nsfw           # skip NSFW sites
        """
    )

    parser.add_argument("target", help="Email address or username to scan")

    parser.add_argument(
        "--mode", choices=["email", "username", "auto"], default="auto",
        help="Force scan mode (default: auto-detect)"
    )
    parser.add_argument(
        "--both", action="store_true",
        help="Run both holehe AND user-scanner (useful when target is an email)"
    )
    parser.add_argument(
        "--only-found", action="store_true",
        help="Only display sites where the target was found"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show more details (URLs, etc.)"
    )
    parser.add_argument(
        "-c", "--category",
        help="Scan only a specific category (username mode only)"
    )
    parser.add_argument(
        "--no-nsfw", action="store_true",
        help="Skip adult/NSFW sites"
    )
    parser.add_argument(
        "-t", "--timeout", type=int, default=10,
        help="Request timeout in seconds (default: 10)"
    )

    args = parser.parse_args()
    print_banner()

    target = args.target.strip()

    if args.mode == "auto":
        mode = "email" if is_email(target) else "username"
    else:
        mode = args.mode

    start = time.time()

    if mode == "email":
        print(f"{C}[*] Mode: {Y}Email OSINT (holehe){X}")
        if not is_email(target):
            print(f"{R}[!] '{target}' does not look like a valid email address.{X}")
            sys.exit(1)
        trio_run = __import__("trio")
        trio_run.run(run_holehe, target, args.only_found, args.timeout)

        if args.both:
            print(f"\n{C}[*] Running username scan on '{target.split('@')[0]}' too...{X}")
            run_user_scanner(
                username=target.split("@")[0],
                only_found=args.only_found,
                verbose=args.verbose,
                category=args.category,
                no_nsfw=args.no_nsfw,
            )
    else:
        print(f"{C}[*] Mode: {Y}Username Scanner{X}")
        run_user_scanner(
            username=target,
            only_found=args.only_found,
            verbose=args.verbose,
            category=args.category,
            no_nsfw=args.no_nsfw,
        )

    elapsed = round(time.time() - start, 2)
    print(f"\n{B}{C}[✓] Done in {elapsed}s{X}\n")


if __name__ == "__main__":
    main()
