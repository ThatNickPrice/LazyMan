import os
import re
import sys
import time
from urllib.parse import parse_qsl, quote

import xbmc
import xbmcgui
import xbmcplugin

from resources.lib import highlights
from resources.lib import utils
from resources.lib.game import GameBuilder
from resources.lib.utils import log, _requests, add_list
from resources.lib.vars import (
    ADDON,
    ADDONID,
    ADDONNAME,
    ADDONHANDLE,
    BASE_URL,
    CDN,
    ICON,
    SHOWALLDAYS,
    STRM_QUALITY,
    USER_AGENT,
)


def games(date, provider, getfeeds=False):
    return GameBuilder.fromDate(date, GameBuilder.Remaining, provider, getfeeds)


def list_year(provider):
    for y in utils.years(provider):
        add_list(y, "listmonths", provider, year=y)


def list_month(y, provider):
    for (mn, m) in utils.months(y):
        add_list(mn, "listdays", provider, year=y, month=m)


def list_day(y, m, provider):
    for d in utils.days(y, m):
        add_list(d, "listgames", provider, year=y, month=m, day=d)


def menu():
    for provider in ("NHL.tv", "MLB.tv"):
        leaders = highlights.get_leaders(provider)
        thumb = highlights.random_image(provider)
        add_list(provider, 'listtodaysgames', provider, desc=leaders, icon=thumb)


def list_games(date, provider, previous=False, highlight=False):
    dategames = games(date, provider)

    if len(dategames) < 1:
        xbmcplugin.endOfDirectory(ADDONHANDLE, succeeded=False)
        xbmcgui.Dialog().ok(ADDONNAME, "No games scheduled today")
        if not previous:
            return

    for g in dategames:
        label = (
            f"{g.awayFull} vs. {g.homeFull} "
            f"[{g.remaining if g.remaining != 'Scheduled' else utils.asCurrentTz(date, g.time)}]"
        )
        add_list(label, 'listfeeds', provider, date=date, gid=g.gid, desc=g.desc, icon=g.thumb)

    if previous:
        action = "yesterday"
        title = "[I]Yesterdays Games[/I]"
        if SHOWALLDAYS:
            action = "listyears"
            title = "[I]Previous Games[/I]"
        add_list(title, action, provider)

    if highlight:
        add_list("[I]Highlights[/I]", "listhighlightsgroup", provider)


def list_feeds(game, date, provider):
    def getfeedicon(feed):
        feed = p.sub("", feed)
        log(f"FeedIcon: {feed}", debug=True)
        return os.path.join('special://home', 'addons', ADDONID, 'resources', 'icons', feed + '.png')

    p = re.compile(r" \((Home|Away|National|French)\)|\.(com|tv)| Camera|2|[ +-]")
    for f in [f for f in game.feeds if f.viewable()]:
        label = str(f)
        icon = getfeedicon(label)
        add_list(label, "playgame", provider, date=date, gid=f.mediaId, state=game.gameState, icon=icon, isStream=True)


def highlights_menu(provider):
    if provider == "NHL.tv":
        for hg in highlights.get_nhl_highlights():
            add_list(hg.title, "listhighlights", provider, group=hg.title)

    add_list("Game Recaps", "listrecaps", provider, state=1)  # state=page
    add_list("Team Videos", "listteams", provider)


def list_highlights(provider, group):
    for hg in [x for x in highlights.get_nhl_highlights() if x.title == group]:
        for h in hg.highlights:
            label = f"{h.blurb} ({h.duration})"
            add_list(label, "playhighlight", url=h.playbackUrl, desc=h.desc, icon=h.thumb, isStream=True)


def get_stream(date, feed, provider, state):
    def adjustQuality(masterUrl):
        if STRM_QUALITY == "Master":
            return masterUrl

        quality = {'540p':   '2500K/2500_{0}.m3u8',
                   '720p':   '3500K/3500_{0}.m3u8',
                   '720p60': '5600K/5600_{0}.m3u8'}

        m3u8Path = quality.get(STRM_QUALITY).format(
            "complete"
            if re.search(r"Progress|Scheduled|Pre-Game|Warmup", state)
            else "complete-trimmed"
        )

        log(f"AdjustedQuality: {m3u8Path}", debug=True)
        return f"{masterUrl.rsplit('/', 1)[0]}/{m3u8Path}"

    log(f"GameState: {state}", debug=True)
    url = f"https://{BASE_URL}/mlb/m3u8/{date}/{feed}{CDN}"
    contentUrl = url.replace('mlb/', '') if provider == "NHL.tv" else url

    log(f"Checking contentUrl: {contentUrl}", debug=True)
    if not utils.head(contentUrl):
        log("Invalid contentUrl")
        xbmcplugin.endOfDirectory(ADDONHANDLE, succeeded=False)
        xbmcgui.Dialog().ok(ADDONNAME, "Game not available yet")
        return

    url = _requests().get(contentUrl, timeout=3).text
    log(f"Stream URL resolved: {url}", debug=True)

    if not utils.head(url):
        xbmcplugin.endOfDirectory(ADDONHANDLE, succeeded=False)
        xbmcgui.Dialog().ok(ADDONNAME, "Stream is unavailable")
        return
    play(adjustQuality(url))


def play(url, mode=None, highlight=False):
    item = xbmcgui.ListItem(path=url)
    item.setMimeType('application/x-mpegURL')

    auth_header = f"mediaAuth%3D%22{utils.salt()}%22"
    url = f"{url}|cookie={auth_header}&user-agent={quote(USER_AGENT)}"

    # NOTE: inputstream/curl fails on ssl cert domain mismatch so we cant use it for game feeds
    # TODO: try streamlink when its updated for v19
    if highlight:
        item.setPath(url.split('|')[0])
        item.setProperty('inputstreamclass', 'inputstream.adaptive')
        item.setProperty('inputstream.adaptive.manifest_type', 'hls')
        item.setProperty('inputstream.adaptive.stream_headers', url.split('|')[1])

    xbmcplugin.setResolvedUrl(ADDONHANDLE, True, item)


def dnsCheck():
    # time (in hours) between checking dns entries
    if int(time.time() - (24 * 3600)) > ADDON.getSettingInt("dnsChecked"):
        lazymanServer = utils.resolve(BASE_URL)
        # check if server is alive
        if not lazymanServer or not utils.isUp(lazymanServer):
            xbmcgui.Dialog().ok(ADDONNAME, "The Lazyman Server is Offline.")
            return
        xbmc.executebuiltin(f"Notification(LazyMan,Checking DNS...,,{ICON})")
        for host in (
            "mf.svc.nhl.com",
            "mlb-ws-mf.media.mlb.com",
            "playback.svcs.mlb.com",
        ):
            # check if dns entries are redirected properly
            resolved = utils.resolve(host)
            if resolved != lazymanServer:
                xbmcgui.Dialog().ok(
                    ADDONNAME,
                    f"{host} doesn't resolve to the Lazyman server.",
                    f"Update your hosts file to point to {lazymanServer}",
                )
            else:
                ADDON.setSettingInt("dnsChecked", int(time.time()))


params = dict(parse_qsl(sys.argv[2][1:]))
action = params['action'] if 'action' in params else None
cacheToDisc = True

if action is None:
    dnsCheck()
    menu()

elif action == "listtodaysgames":
    list_games(utils.today().strftime("%Y-%m-%d"), params['mode'], True, True)
    cacheToDisc = False
elif action == "listgames":
    list_games(f"{int(params['year'])}-{int(params['month']):02}-{int(params['day']):02}", params['mode'])
elif action == "listfeeds":
    list_feeds({g.gid: g for g in games(params['date'], params['mode'], getfeeds=True)}
        [int(params['gid'])], params['date'], params['mode'])

elif action == "playgame":
    get_stream(params['date'], params['gid'], params['mode'], params['state'])
elif action == "playhighlight":
    play(params['url'], params['mode'], True)

elif action == "listhighlightsgroup":
    highlights_menu(params['mode'])
elif action == "listhighlights":
    list_highlights(params['mode'], params['group'])
elif action == "listrecaps":
    highlights.get_recaps(params['mode'], int(params['state']))

elif action == "listteams":
    highlights.teamList(params['mode'])
elif action == "listteam":
    highlights.teamTopics(params['url'], params['mode'])
elif action == "listteam_subdir":
    highlights.teamSub(params['url'], params['mode'])

elif action == "listyears":
    list_year(params['mode'])
elif action == "listmonths":
    list_month(params['year'], params['mode'])
elif action == "listdays":
    list_day(params['year'], params['month'], params['mode'])
elif action == "yesterday":
    list_games(utils.today(1).strftime("%Y-%m-%d"), params['mode'], False, True)

xbmcplugin.endOfDirectory(ADDONHANDLE, cacheToDisc=cacheToDisc)
