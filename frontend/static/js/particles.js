/* SynapseTwin — Plexus Particle Background
   Self-initialising: creates its own canvas, appends to body.
   Works on both light and dark pages. */
(function () {
  "use strict";

  /* ── Canvas setup ──────────────────────────────────────────────────────── */
  var canvas = document.createElement("canvas");
  canvas.id = "particles-bg";
  canvas.style.cssText =
    "position:fixed;top:0;left:0;width:100%;height:100%;" +
    "z-index:0;pointer-events:none;";
  document.body.insertBefore(canvas, document.body.firstChild);

  /* Ensure all content sits above the canvas */
  var style = document.createElement("style");
  style.textContent =
    ".app-shell{position:relative;z-index:1;}" +
    ".auth-wrap{position:relative;z-index:1;}" +
    "#toast-container{z-index:9999!important;}";
  document.head.appendChild(style);

  var ctx = canvas.getContext("2d");
  var W, H;

  /* ── Config ────────────────────────────────────────────────────────────── */
  var CFG = {
    count: 350,
    speed: 0.35,
    maxDist: 160,
    dotMinR: 1.5,
    dotMaxR: 3.5,
    /* Purple/indigo palette — readable on the soft-white light theme */
    dotRgb: "109,40,217",
    lineRgb: "109,40,217",
    maxDotAlpha: 0.5,
    minDotAlpha: 0.15,
    maxLineAlpha: 0.2,
  };

  /* ── Particle class ────────────────────────────────────────────────────── */
  function Particle(init) {
    this.r = CFG.dotMinR + Math.random() * (CFG.dotMaxR - CFG.dotMinR);
    this.opacity =
      CFG.minDotAlpha + Math.random() * (CFG.maxDotAlpha - CFG.minDotAlpha);
    this.reset(init);
  }

  Particle.prototype.reset = function (init) {
    this.x = Math.random() * W;
    this.y = init ? Math.random() * H : Math.random() < 0.5 ? -12 : H + 12;
    this.vx = (Math.random() - 0.5) * CFG.speed * 2;
    this.vy = (Math.random() - 0.5) * CFG.speed * 2;
    /* add a tiny drift so they never freeze */
    if (Math.abs(this.vx) < 0.05)
      this.vx = CFG.speed * 0.3 * (this.vx < 0 ? -1 : 1);
    if (Math.abs(this.vy) < 0.05)
      this.vy = CFG.speed * 0.3 * (this.vy < 0 ? -1 : 1);
  };

  Particle.prototype.tick = function () {
    this.x += this.vx;
    this.y += this.vy;
    if (this.x < -20 || this.x > W + 20 || this.y < -20 || this.y > H + 20) {
      this.reset(false);
    }
  };

  Particle.prototype.draw = function () {
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(" + CFG.dotRgb + "," + this.opacity + ")";
    ctx.fill();
  };

  /* ── Init ──────────────────────────────────────────────────────────────── */
  var particles = [];

  function resize() {
    W = canvas.width = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  function init() {
    resize();
    particles = [];
    for (var i = 0; i < CFG.count; i++) {
      particles.push(new Particle(true));
    }
  }

  /* ── Render loop ───────────────────────────────────────────────────────── */
  function frame() {
    ctx.clearRect(0, 0, W, H);

    /* connections */
    for (var i = 0; i < particles.length; i++) {
      for (var j = i + 1; j < particles.length; j++) {
        var dx = particles[i].x - particles[j].x;
        var dy = particles[i].y - particles[j].y;
        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < CFG.maxDist) {
          var alpha = (1 - dist / CFG.maxDist) * CFG.maxLineAlpha;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = "rgba(" + CFG.lineRgb + "," + alpha + ")";
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
    }

    /* dots */
    for (var k = 0; k < particles.length; k++) {
      particles[k].tick();
      particles[k].draw();
    }

    requestAnimationFrame(frame);
  }

  /* ── Bootstrap ─────────────────────────────────────────────────────────── */
  window.addEventListener("resize", function () {
    resize();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init();
      frame();
    });
  } else {
    init();
    frame();
  }
})();
