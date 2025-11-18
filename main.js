// static/js/main.js

document.addEventListener('DOMContentLoaded', function() {
    console.log('main.js loaded. DOMContentLoaded fired.');

    const currentUsername = document.body.dataset.currentUsername;
    console.log('Current username (from data attribute):', currentUsername);

    // GSAP Animations for index.html (existing logic)
    const sections = document.querySelectorAll('.gs_reveal');
    if (sections.length > 0) {
        console.log('GSAP: Found .gs_reveal sections for animation.');
        sections.forEach((section, index) => {
            let fromProps = {};
            if (section.classList.contains('gs_reveal_fromBottom')) {
                fromProps.y = 100;
            } else if (section.classList.contains('gs_reveal_fromLeft')) {
                fromProps.x = -100;
            } else if (section.classList.contains('gs_reveal_fromRight')) {
                fromProps.x = 100;
            }

            gsap.fromTo(section,
                { ...fromProps, opacity: 0, visibility: 'hidden' },
                {
                    opacity: 1,
                    y: 0,
                    x: 0,
                    visibility: 'visible',
                    duration: 1.2,
                    delay: index * 0.2,
                    ease: 'power3.out'
                }
            );
        });
    } else {
        console.log('GSAP: No .gs_reveal sections found for animation on this page.');
    }

    // All DR Risk Assessment JS logic is now in patient_view.html
    // All Analytics JS logic is now in analytics.html
});
