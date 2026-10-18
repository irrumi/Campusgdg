/* A classic script makes module-loading failures visible before the app starts. */
(() => {
  const status = document.getElementById("service-label");
  if (status) status.textContent = "Starting workspace";
  document.documentElement.dataset.campuspulseBoot = "loading";

  import("./app.mjs?v=20261017-1").then(() => {
    document.documentElement.dataset.campuspulseBoot = "ready";
  }).catch(() => {
    document.documentElement.dataset.campuspulseBoot = "failed";
    if (status) status.textContent = "Interface unavailable";
    const notice = document.getElementById("error");
    if (notice) {
      notice.textContent = "The interface could not start. Reload this page. If the problem continues, check that the server serves JavaScript modules with the correct content type.";
      notice.hidden = false;
    }
    console.error("CampusPulse interface failed to start. Check JavaScript module delivery.");
  });
})();
