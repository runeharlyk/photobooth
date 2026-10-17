<script lang="ts">
  let { remaining }: { remaining: number | null } = $props()

  const preview = `/api/preview.mjpg?t=${Date.now()}`
</script>

<div class="countdown">
  <img src={preview} alt="" />
  <div class="corner circle" style:--d="92px" aria-live="polite">{remaining ?? ''}</div>
</div>

<style>
  .countdown {
    position: absolute;
    inset: 0;
  }

  img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    transform: scaleX(-1);
  }

  .corner {
    position: absolute;
    right: 22px;
    bottom: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #000;
    font-family: 'Archivo Black', sans-serif;
    font-size: calc(var(--d) * 0.59);
    animation: travel 600ms cubic-bezier(0.5, 0, 0.2, 1);
  }

  @keyframes travel {
    from {
      transform: translate(-572px, -332px) scale(2.5);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .corner {
      animation: none;
    }
  }
</style>
