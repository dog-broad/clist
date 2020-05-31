<?php
    require_once dirname(__FILE__) . "/../../config.php";

    $page = curlexec($URL);
    $URLS = array();

    //if (!preg_match('#href="\#nogo">.*?<li><a href="(?<url>[^"]+)">.*?</a></li>#s', $page, $match)) return;
    //$url = (strpos($match['url'], 'http://') !== 0? $URL : '') . $match['url'];
    //$URLS[] = $url;
    if (preg_match_all('#<a[^>]*href="(?<url>[^"]*sn[sw]s\d*(?P<y>\d\d)[^"]*)"[^>]*>#s', $page, $matches, PREG_SET_ORDER)) {
        $max = -1;
        $url = NULL;
        foreach ($matches as $m) {
            $y = (int)$m['y'];
            $u = $m['url'];
            $u = (strpos($u, 'http://') !== 0? $u : '') . $u;
            $u = rtrim($u, '/');

            if ($y > $max) {
                $max = $y;
                $url = $m['url'];
            }

            if (isset($_GET['parse_full_list'])) {
                $URLS[] = $u;
            }
        }
        $url = (strpos($url, 'http://') !== 0? $URL : '') . $url;
        $url = rtrim($url, '/');
        $URLS[] = $url;
    }
    $year = date('Y');
    for ($i = 0; $i < 10; ++$i) {
        $y = $year - $i;
        $URLS[] = "http://snws$y.snarknews.info";
        $URLS[] = "http://snss$y.snarknews.info";
        if (!isset($_GET['parse_full_list'])) {
            break;
        }
    }
    sort($URLS);
    $URLS = array_unique($URLS);
    foreach ($URLS as $url)
    {
        $page = curlexec($url);

        $schedule_url = false;

        if (preg_match('#a href="(?<url>index.cgi?[^"]*schedule[^"]*)"#s', $page, $match)) {
            $schedule_url = (strpos($match['url'], 'http://') !== 0? $URL : '') . $match['url'];
        }

        $standings_url = array();
        if (preg_match('#<a[^>]*href="(?P<url>[^"]*global[^"]*)"[^>]*>#i', $page, $match)) {
            $u = $match['url'];
            $p = curlexec($u);

            if (!$schedule_url) {
                $schedule_url = str_replace('global', 'schedule', $u);
            }

            preg_match_all('#<a[^>]*href="(?P<url>[^"]*standing[^"]*)"[^>]*>(?P<name>Round[^<]*(?P<round>[0-9]+))#', $p, $matches, PREG_SET_ORDER);
            foreach ($matches as $match) {
                $round = intval($match['round']);
                $standings_url[$round] = url_merge($u, $match['url']);
            }
        }

        if (!$schedule_url) {
            continue;
        }

        $page = curlexec($schedule_url);

        if (!preg_match('#<td class="maintext">[^<]*<center>[^<]*<table border=1>.*?</table>#s', $page, $match)) continue;
        $table = $match[0];

        if (!preg_match('#<font[^>]*>(?<title>.*?)</font>#', $page, $match)) continue;
        $event_title = ucwords(trim(strip_tags($match['title'])));

        preg_match_all('#href="(?<url>http://(?:algorithm\.)?contest[0-9]*\.yandex\.ru/(?:[a-z]+[0-9]+/)?contest/(?P<id>\d+)/enter)"#', $page, $matches, PREG_SET_ORDER);
        usort($matches, function ($a, $b) { return intval($a['id']) < intval($b['id'])? -1 : 1; });
        $urls = [];
        foreach ($matches as $m) {
            $urls[] = str_replace('contest2.yandex', 'contest.yandex', $m['url']);
        }
        $urls = array_unique($urls);

        preg_match_all('#<tr>[^<]*<td>(?<title>.*?)</td>[^<]*<td>[^0-9]*(?<start_time>.*?)[^0-9]*</td>[^<]*<td>[^0-9]*(?<end_time>.*?)[^0-9]*</td>.*?</tr>#s', $table, $matches);

        foreach ($matches[0] as $i => $value)
        {
            $title = trim(strip_tags($matches['title'][$i])) . '. ' . $event_title;

            $contest = array(
                'title' => $title,
                'start_time' => trim(strip_tags($matches['start_time'][$i])),
                'end_time' => trim(strip_tags($matches['end_time'][$i])),
                'duration_in_secs' => (60 + 20) * 60,
                'url' => $i < count($urls)? $urls[$i] : $url,
                'host' => $HOST,
                'rid' => $RID,
                'timezone' => $TIMEZONE,
                'key' => $title
            );

            if (preg_match('#[0-9]+#', $title, $match)) {
                $round = intval($match[0]);
                if (isset($standings_url[$round])) {
                    $contest['standings_url'] = $standings_url[$round];
                    unset($standings_url[$round]);
                }
            }

            $contests[] = $contest;
        }
    }
    if ($RID == -1) {
        // print_r($contests);
    }
?>
