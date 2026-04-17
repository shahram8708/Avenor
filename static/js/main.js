(() => {
    const waitlistCounterEl = document.getElementById("waitlistCounter");
    const stickyCountEl = document.getElementById("stickyWaitlistCount");
    const stickyBarEl = document.getElementById("stickyCtaBar");
    const navbarEl = document.getElementById("mainNavbar");
    const statNumberEls = document.querySelectorAll(".stat-number");
    const serviceSearchInput = document.getElementById("serviceSearch");
    const noServiceResultsEl = document.getElementById("noServiceResults");
    const categoryToggles = document.querySelectorAll(".category-toggle");

    let counterValue = 0;

    const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

    const animateNumber = (el, from, to, duration = 2000, suffix = "") => {
        if (!el) {
            return;
        }
        const start = performance.now();

        const tick = (now) => {
            const elapsed = now - start;
            const progress = Math.min(elapsed / duration, 1);
            const eased = easeOutCubic(progress);
            const value = Math.round(from + (to - from) * eased);
            el.textContent = `${value}${suffix}`;
            if (progress < 1) {
                window.requestAnimationFrame(tick);
            }
        };

        window.requestAnimationFrame(tick);
    };

    const updateDisplayedWaitlistCount = (newCount, animate = true) => {
        const safeCount = Number.isFinite(newCount) ? Math.max(newCount, 0) : 0;
        if (waitlistCounterEl) {
            if (animate) {
                animateNumber(waitlistCounterEl, counterValue, safeCount, 2000);
            } else {
                waitlistCounterEl.textContent = String(safeCount);
            }
        }

        if (stickyCountEl) {
            stickyCountEl.textContent = String(safeCount);
        }

        counterValue = safeCount;
    };

    const fetchWaitlistCount = async (animate = true) => {
        try {
            const response = await fetch("/api/waitlist-count", {
                headers: { "Accept": "application/json" },
                cache: "no-store",
            });
            if (!response.ok) {
                return;
            }
            const payload = await response.json();
            const count = Number(payload.count || 0);
            updateDisplayedWaitlistCount(count, animate);
        } catch (error) {
            void error;
        }
    };

    const handleScrollUi = () => {
        const y = window.scrollY || window.pageYOffset;

        if (navbarEl) {
            navbarEl.classList.toggle("scrolled", y > 50);
        }

        if (stickyBarEl) {
            stickyBarEl.classList.toggle("visible", y > 600);
            stickyBarEl.setAttribute("aria-hidden", y > 600 ? "false" : "true");
        }
    };

    const initStatsObserver = () => {
        if (!statNumberEls.length) {
            return;
        }

        const runCounter = (el) => {
            const target = Number(el.getAttribute("data-target") || "0");
            const suffix = el.parentElement?.textContent?.toLowerCase().includes("speed") ? "x" : "";
            animateNumber(el, 0, target, 1600, suffix);
        };

        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        const targetEl = entry.target;
                        if (!targetEl.classList.contains("counted")) {
                            runCounter(targetEl);
                            targetEl.classList.add("counted");
                        }
                        observer.unobserve(targetEl);
                    }
                });
            },
            { threshold: 0.45 }
        );

        statNumberEls.forEach((el) => observer.observe(el));
    };

    const initServiceSearch = () => {
        if (!serviceSearchInput) {
            return;
        }

        const categoryEls = Array.from(document.querySelectorAll(".service-category"));

        serviceSearchInput.addEventListener("input", () => {
            const query = serviceSearchInput.value.trim().toLowerCase();
            let visibleBadges = 0;

            categoryEls.forEach((categoryEl) => {
                const badges = Array.from(categoryEl.querySelectorAll(".service-badge"));
                let categoryVisibleCount = 0;

                badges.forEach((badge) => {
                    const text = badge.textContent.toLowerCase();
                    const matches = !query || text.includes(query);
                    badge.style.display = matches ? "inline-block" : "none";
                    if (matches) {
                        categoryVisibleCount += 1;
                    }
                });

                if (categoryVisibleCount > 0) {
                    categoryEl.style.display = "";
                    visibleBadges += categoryVisibleCount;
                } else {
                    categoryEl.style.display = "none";
                }
            });

            if (noServiceResultsEl) {
                noServiceResultsEl.classList.toggle("d-none", visibleBadges > 0);
            }
        });
    };

    const initCategoryToggles = () => {
        if (!categoryToggles.length) {
            return;
        }

        categoryToggles.forEach((toggleBtn) => {
            const targetSelector = toggleBtn.getAttribute("data-bs-target");
            if (!targetSelector) {
                return;
            }
            const collapseEl = document.querySelector(targetSelector);
            if (!collapseEl) {
                return;
            }

            const setLabel = () => {
                toggleBtn.textContent = collapseEl.classList.contains("show") ? "Collapse" : "Show All";
            };

            toggleBtn.addEventListener("click", () => {
                const instance = bootstrap.Collapse.getOrCreateInstance(collapseEl, {
                    toggle: false,
                });
                if (collapseEl.classList.contains("show")) {
                    instance.hide();
                } else {
                    instance.show();
                }
            });

            collapseEl.addEventListener("shown.bs.collapse", setLabel);
            collapseEl.addEventListener("hidden.bs.collapse", setLabel);
            setLabel();
        });
    };

    const initSmoothScroll = () => {
        const anchors = document.querySelectorAll("a[href*='#']");
        anchors.forEach((anchor) => {
            anchor.addEventListener("click", (event) => {
                const href = anchor.getAttribute("href") || "";
                if (!href.includes("#")) {
                    return;
                }

                const url = new URL(href, window.location.origin);
                if (url.pathname !== window.location.pathname || !url.hash) {
                    return;
                }

                const target = document.querySelector(url.hash);
                if (!target) {
                    return;
                }

                event.preventDefault();
                target.scrollIntoView({ behavior: "smooth", block: "start" });
                history.replaceState(null, "", url.hash);
            });
        });
    };

    const initFormValidation = () => {
        const forms = document.querySelectorAll(".waitlist-form");
        if (!forms.length) {
            return;
        }

        const emailRegex = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$/;

        forms.forEach((form) => {
            const requiredFields = form.querySelectorAll("input[required]");
            const submitButton = form.querySelector("button[type='submit']");
            const btnLabel = submitButton?.querySelector(".btn-label");
            const btnLoading = submitButton?.querySelector(".btn-loading");

            requiredFields.forEach((field) => {
                field.addEventListener("input", () => {
                    field.classList.remove("is-invalid");
                });
            });

            form.addEventListener("submit", (event) => {
                let valid = true;
                requiredFields.forEach((field) => {
                    const value = field.value.trim();
                    if (!value) {
                        field.classList.add("is-invalid");
                        valid = false;
                    } else if (field.type === "email" && !emailRegex.test(value)) {
                        field.classList.add("is-invalid");
                        valid = false;
                    } else {
                        field.classList.remove("is-invalid");
                    }
                });

                if (!valid) {
                    event.preventDefault();
                    return;
                }

                if (submitButton) {
                    submitButton.disabled = true;
                    submitButton.classList.add("disabled");
                }
                if (btnLabel) {
                    btnLabel.classList.add("d-none");
                }
                if (btnLoading) {
                    btnLoading.classList.remove("d-none");
                }
            });
        });
    };

    const initStickyBarScrollButton = () => {
        if (!stickyBarEl) {
            return;
        }

        const ctaLink = stickyBarEl.querySelector("a[href='#join-waitlist']");
        if (!ctaLink) {
            return;
        }

        ctaLink.addEventListener("click", (event) => {
            const target = document.getElementById("join-waitlist");
            if (!target) {
                return;
            }
            event.preventDefault();
            target.scrollIntoView({ behavior: "smooth", block: "start" });
        });
    };

    const init = () => {
        const defaultCount = Number(waitlistCounterEl?.dataset.count || stickyCountEl?.textContent || "0");
        updateDisplayedWaitlistCount(defaultCount, false);
        fetchWaitlistCount(true);
        window.setInterval(() => fetchWaitlistCount(false), 30000);

        handleScrollUi();
        window.addEventListener("scroll", handleScrollUi, { passive: true });

        initStatsObserver();
        initServiceSearch();
        initCategoryToggles();
        initSmoothScroll();
        initFormValidation();
        initStickyBarScrollButton();
    };

    document.addEventListener("DOMContentLoaded", init);
})();
