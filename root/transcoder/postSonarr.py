#!/usr/bin/env python3
import os
import sys
import requests
import time
import shutil
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.metadata import MediaType
from resources.mediaprocessor import MediaProcessor


# Sonarr API functions
def rescanAndWait(baseUrl, headers, seriesId, log, retries=6, delay=10):
    url = baseUrl + "/api/v3/command"
    log.debug("Queueing rescan command to Sonarr via %s." % url)

    # First trigger rescan
    payload = {'name': 'RescanSeries', 'seriesId': seriesId}
    log.debug(str(payload))

    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.debug(str(rstate))
    log.info("Sonarr response from RescanSeries command: ID %d %s." % (rstate['id'], rstate['status']))

    # Then wait for it to finish
    url = baseUrl + "/api/v3/command/" + str(rstate['id'])
    log.debug("Requesting command status from Sonarr for command ID %d." % rstate['id'])
    r = requests.get(url, headers=headers)
    command = r.json()

    attempts = 0
    while command['status'].lower() not in ['complete', 'completed'] and attempts < retries:
        log.debug("Status: %s." % (command['status']))
        time.sleep(delay)
        r = requests.get(url, headers=headers)
        command = r.json()
        attempts += 1
    log.debug(str(command))
    log.debug("Final status: %s." % (command['status']))
    return command['status'].lower() in ['complete', 'completed']


def renameSeriesRequest(baseUrl, headers, seriesId, log):
    url = baseUrl + "/api/v3/command"
    log.debug("Queueing rename command to Sonarr via %s." % url)

    payload = {'name': 'RenameSeries', 'seriesIds': [seriesId]}
    log.debug(str(payload))
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    return rstate


def downloadedEpisodesScanInProgress(baseUrl, headers, episodeFileSourceFolder, log):
    url = baseUrl + "/api/v3/command"
    log.debug("Requesting commands in process from Sonarr via %s." % url)
    r = requests.get(url, headers=headers)
    commands = r.json()
    log.debug(commands)
    log.debug(episodeFileSourceFolder)
    for c in commands:
        if c.get('name') == "DownloadedEpisodesScan":
            try:
                if c['body']['path'] == episodeFileSourceFolder and c['status'] == 'started':
                    log.debug("Found a matching path scan in progress %s." % (episodeFileSourceFolder))
                    return True
            except:
                pass
    log.debug("No commands in progress for %s." % (episodeFileSourceFolder))
    return False


def getEpisode(baseUrl, headers, episodeId, log):
    url = baseUrl + "/api/v3/episode/" + str(episodeId)
    log.debug("Requesting episode from Sonarr via %s." % url)
    r = requests.get(url, headers=headers)
    payload = r.json()
    log.debug(str(payload))
    return payload


def updateEpisode(baseUrl, headers, new, episodeId, log):
    url = baseUrl + "/api/v3/episode/" + str(episodeId)
    log.debug("Requesting episode update to Sonarr via %s." % url)
    r = requests.put(url, json=new, headers=headers)
    payload = r.json()
    return payload


def getEpisodeFile(baseUrl, headers, episodeFileId, log):
    url = baseUrl + "/api/v3/moviefile/" + str(episodeFileId)
    log.debug("Requesting moviefile from Sonarr for moviefile via %s." % url)
    r = requests.get(url, headers=headers)
    payload = r.json()
    return payload


def updateEpisodeFile(baseUrl, headers, new, episodeFileId, log):
    url = baseUrl + "/api/v3/moviefile/" + str(episodeFileId)
    log.debug("Requesting moviefile update to Sonarr via %s." % url)
    r = requests.put(url, json=new, headers=headers)
    payload = r.json()
    return payload


# Rename functions
def renameFile(inputFile, log):
    filename, fileExt = os.path.splitext(inputFile)
    outputFile = "%s.rnm%s" % (filename, fileExt)
    i = 2
    while os.path.isfile(outputFile):
        outputFile = "%s.rnm%d%s" % (filename, i, fileExt)
        i += 1
    os.rename(inputFile, outputFile)
    log.debug("Renaming file %s to %s." % (inputFile, outputFile))
    return outputFile


def restoreSceneName(inputFile, sceneName):
    if sceneName:
        directory = os.path.dirname(inputFile)
        extension = os.path.splitext(inputFile)[1]
        os.rename(inputFile, os.path.join(directory, "%s%s" % (sceneName, extension)))


def backupSubs(inputpath, mp, log, extension=".backup"):
    dirname, filename = os.path.split(inputpath)
    files = []
    output = {}
    for r, _, f in os.walk(dirname):
        for file in f:
            files.append(os.path.join(r, file))
    for filePath in files:
        if filePath.startswith(os.path.splitext(filename)[0]):
            info = mp.isValidSubtitleSource(filePath)
            if info:
                newPath = filePath + extension
                shutil.copy2(filePath, newPath)
                output[newPath] = filePath
                log.info("Copying %s to %s." % (filePath, newPath))
    return output


def restoreSubs(subs, log):
    for k in subs:
        try:
            os.rename(k, subs[k])
            log.info("Restoring %s to %s." % (k, subs[k]))
        except:
            os.remove(k)
            log.exception("Unable to restore %s, deleting." % (k))


log = getLogger("SonarrPostProcess")

log.info("Sonarr extra script post processing started.")

if os.environ.get('sonarr_eventtype') == "Test":
    sys.exit(0)

settings = ReadSettings()

log.debug(os.environ)

try:
    inputFile = os.environ.get('sonarr_episodefile_path')
    original = os.environ.get('sonarr_episodefile_scenename')
    tvdbId = int(os.environ.get('sonarr_series_tvdbid'))
    imdbId = os.environ.get('sonarr_series_imdbid')
    season = int(os.environ.get('sonarr_episodefile_seasonnumber'))
    seriesId = int(os.environ.get('sonarr_series_id'))
    sceneName = os.environ.get('sonarr_episodefile_scenename')
    releaseGroup = os.environ.get('sonarr_episodefile_releasegroup')
    episodeFileId = os.environ.get('sonarr_episodefile_id')
    episodeFileSourceFolder = os.environ.get('sonarr_episodefile_sourcefolder')
    episode = int(os.environ.get('sonarr_episodefile_episodenumbers').split(",")[0])
    episodeId = int(os.environ.get('sonarr_episodefile_episodeids').split(",")[0])
except:
    log.exception("Error reading environment variables")
    sys.exit(1)

mp = MediaProcessor(settings)

log.debug("Input file: %s." % inputFile)
log.debug("Original name: %s." % original)
log.debug("TVDB ID: %s." % tvdbId)
log.debug("Season: %s episode: %s." % (season, episode))
log.debug("Sonarr series ID: %d." % seriesId)

try:
    if settings.Sonarr.get('rename'):
        # Prevent asynchronous errors from file name changing
        mp.settings.waitpostprocess = True
        try:
            inputFile = renameFile(inputFile, log)
        except:
            log.exception("Error renaming inputFile.")

    success = mp.fullprocess(inputFile, MediaType.TV, tvdbId=tvdbId, imdbId=imdbId, season=season, episode=episode, original=original)

    if success and not settings.Sonarr['rescan']:
        log.info("File processed successfully and rescan API update disabled.")
    elif success:
        # Update Sonarr to continue monitored status
        try:
            host = settings.Sonarr['host']
            port = settings.Sonarr['port']
            webroot = settings.Sonarr['webroot']
            apiKey = settings.Sonarr['apikey']
            ssl = settings.Sonarr['ssl']
            protocol = "https://" if ssl else "http://"
            baseUrl = protocol + host + ":" + str(port) + webroot

            log.debug("Sonarr baseUrl: %s." % baseUrl)
            log.debug("Sonarr apiKey: %s." % apiKey)

            if apiKey != '':
                headers = {'X-Api-Key': apiKey}

                subs = backupSubs(success[0], mp, log)

                if downloadedEpisodesScanInProgress(baseUrl, headers, episodeFileSourceFolder, log):
                    log.info("DownloadedEpisodesScan command is in process for this episode, cannot wait for rescan but will queue.")
                    rescanAndWait(baseUrl, headers, seriesId, log, retries=0)
                    renameSeriesRequest(baseUrl, headers, seriesId, log)
                elif rescanAndWait(baseUrl, headers, seriesId, log):
                    log.info("Rescan command completed.")

                    sonarrEpInfo = getEpisode(baseUrl, headers, episodeId, log)
                    if not sonarrEpInfo:
                        log.error("No valid episode information found, aborting.")
                        sys.exit(1)

                    if not sonarrEpInfo.get('hasFile'):
                        log.warning("Rescanned episode does not have a file, attempting second rescan.")
                        if rescanAndWait(baseUrl, headers, seriesId, log):
                            sonarrEpInfo = getEpisode(baseUrl, headers, episodeId, log)
                            if not sonarrEpInfo:
                                log.error("No valid episode information found, aborting.")
                                sys.exit(1)
                            if not sonarrEpInfo.get('hasFile'):
                                log.warning("Rescanned episode still does not have a file, will not set to monitored to prevent endless loop.")
                                sys.exit(1)
                            else:
                                log.info("File found after second rescan.")
                        else:
                            log.error("Rescan command timed out.")
                            restoreSubs(subs, log)
                            sys.exit(1)

                    if len(subs) > 0:
                        log.debug("Restoring %d subs and triggering a final rescan." % (len(subs)))
                        restoreSubs(subs, log)
                        rescanAndWait(baseUrl, headers, seriesId, log)

                    # Then set that episode to monitored
                    try:
                        sonarrEpInfo['monitored'] = True
                        sonarrEpInfo = updateEpisode(baseUrl, headers, sonarrEpInfo, episodeId, log)
                        log.info("Sonarr monitoring information updated for episode %s." % sonarrEpInfo['title'])
                    except:
                        log.exception("Failed to restore monitored status to episode.")

                    '''
                    if sceneName or releaseGroup:
                        log.debug("Trying to restore scene information.")
                        try:
                            mf = getEpisodeFile(baseUrl, headers, sonarrEpInfo['episodeFile']['id'], log)
                            mf['sceneName'] = sceneName
                            mf['releaseGroup'] = releaseGroup
                            mf = updateEpisodeFile(baseUrl, headers, mf, sonarrEpInfo['episodeFile']['id'], log)
                            log.debug("Restored releaseGroup to %s." % mf.get('releaseGroup'))
                        except:
                            log.exception("Unable to restore scene information.")
                    '''

                    # Now a final rename step to ensure all release / codec information is accurate
                    try:
                        rename = renameSeriesRequest(baseUrl, headers, seriesId, log)
                        log.info("Sonarr response RenameSeries command: ID %d %s." % (rename['id'], rename['status']))
                    except:
                        log.exception("Failed to trigger Sonarr rename.")
                else:
                    log.error("Rescan command timed out.")
                    sys.exit(1)
            else:
                log.error("Your Sonarr API Key is blank. Update autoProcess.ini to enable status updates.")
        except:
            log.exception("Sonarr monitor status update failed.")
    else:
        log.info("Processing returned False.")
        sys.exit(1)
except:
    log.exception("Error processing file.")
    sys.exit(1)