(function () {
    const top = document.querySelector(".login-bg-video--top");
    const bottom = document.querySelector(".login-bg-video--bottom");

    if (!top || !bottom) return;

    const FADE_SECONDS = 1.1;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    let active = top;
    let standby = bottom;
    let fading = false;

    function safelyPlay(video) {
        const playback = video.play();

        if (playback && typeof playback.catch === "function") {
            playback.catch(function () {});
        }
    }

    function replay(video) {
        try {
            video.currentTime = 0;
        } catch (error) {
            // The video may not have metadata yet.
        }

        safelyPlay(video);
    }

    function crossfade() {
        if (fading || standby.readyState < 2) return;

        fading = true;
        replay(standby);

        top.classList.toggle("is-hidden", standby === bottom);

        window.setTimeout(function () {
            active.pause();

            try {
                active.currentTime = 0;
            } catch (error) {
                // Ignore reset errors while the video is still loading.
            }

            active = standby;
            standby = standby === top ? bottom : top;
            fading = false;
        }, FADE_SECONDS * 1000);
    }

    function watch() {
        if (
            !fading &&
            Number.isFinite(active.duration) &&
            active.duration > FADE_SECONDS &&
            active.currentTime >= active.duration - FADE_SECONDS
        ) {
            crossfade();
        }

        window.requestAnimationFrame(watch);
    }

    if (reduceMotion.matches) {
        top.autoplay = false;
        top.pause();
        return;
    }

    safelyPlay(top);
    window.requestAnimationFrame(watch);

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden && !fading) {
            safelyPlay(active);
        }
    });
})();
