(function(){
  const LINKS = [
    { href: "https://roadstate.club/", label: "Home", desc: "Project mission + overview" },
    { href: "https://app.roadstate.club/", label: "App", desc: "Start / Stop collection UI" },
    { href: "https://app.roadstate.club/verify", label: "Verify", desc: "Sensor + upload verification" },
    { href: "https://app.roadstate.club/admin", label: "Admin", desc: "Ops dashboards" },
    { href: "https://app.roadstate.club/login", label: "Login", desc: "Sign in to your account" }
  ];

  function samePage(url){
    try{
      const a = new URL(url, location.href);
      return (a.origin === location.origin) && (a.pathname.replace(/\/+$/,"") === location.pathname.replace(/\/+$/,""));
    }catch(_){ return false; }
  }

  function renderLinks(){
    return LINKS.map(l => {
      const current = samePage(l.href) ? ' aria-current="page"' : '';
      return `<a class="rs-link" href="${l.href}"${current}>
        <div>
          <div style="font-weight:700">${l.label}</div>
          <small>${l.desc}</small>
        </div>
        <div style="color:#9fb1d6">›</div>
      </a>`;
    }).join("");
  }

  function inject(){
    if (document.querySelector(".rs-topbar")) return;

    const bar = document.createElement("div");
    bar.className = "rs-topbar";
    bar.innerHTML = `
      <div class="rs-topbar-inner">
        <a class="rs-brand" href="https://roadstate.club/">
          <div class="rs-dot"></div>
          <div>
            <div class="rs-brand-title">ROADSTATE</div>
            <div class="rs-brand-sub">privacy-first road condition data</div>
          </div>
        </a>
        <button class="rs-hamburger" type="button" aria-label="Menu">
          <div class="rs-burger" aria-hidden="true">
            <span></span><span></span><span></span>
          </div>
        </button>
      </div>
    `;

    const backdrop = document.createElement("div");
    backdrop.className = "rs-drawer-backdrop";

    const drawer = document.createElement("div");
    drawer.className = "rs-drawer";
    drawer.innerHTML = `
      <div class="rs-drawer-head">
        <div class="rs-drawer-title">Menu</div>
        <button class="rs-close" type="button" aria-label="Close">✕</button>
      </div>
      <div class="rs-nav">${renderLinks()}</div>
      <div style="margin-top:auto;color:#9fb1d6;font:500 12px/1.4 ui-sans-serif,system-ui">
        Tip: Add RoadState to your Home Screen for a more app-like feel.
      </div>
    `;

    document.body.prepend(bar);
    document.body.append(backdrop, drawer);
    document.body.classList.add("rs-page-pad");

    function open(){ document.documentElement.classList.add("rs-open"); }
    function close(){ document.documentElement.classList.remove("rs-open"); }

    bar.querySelector(".rs-hamburger").addEventListener("click", open);
    drawer.querySelector(".rs-close").addEventListener("click", close);
    backdrop.addEventListener("click", close);
    document.addEventListener("keydown", (e)=>{ if(e.key==="Escape") close(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", inject);
  else inject();
})();
