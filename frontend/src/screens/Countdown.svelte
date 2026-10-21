<script lang="ts">
  let { remaining }: { remaining: number | null } = $props()

  const preview = `/api/preview.mjpg?t=${Date.now()}`
</script>

<div class="countdown">
  <img src={preview} alt="" />
  <div class="corner circle" style:--d="calc(var(--corner-d) * 1px)" aria-live="polite">{remaining ?? ''}</div>
</div>

<style>
  .countdown {
    --idle-centre-y: calc((var(--stage-h) - var(--idle-gap) - var(--idle-help)) / 2);
    --corner-centre-x: calc(var(--stage-w) - var(--corner-inset) - var(--corner-d) / 2);
    --corner-centre-y: calc(var(--stage-h) - var(--corner-inset) - var(--corner-d) / 2);

    position: absolute;
    inset: 0;
  }

  img {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .corner {
    position: absolute;
    right: calc(var(--corner-inset) * 1px);
    bottom: calc(var(--corner-inset) * 1px);
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
      transform: translate(
          calc((var(--stage-w) / 2 - var(--corner-centre-x)) * 1px),
          calc((var(--idle-centre-y) - var(--corner-centre-y)) * 1px)
        )
        scale(calc(var(--idle-d) / var(--corner-d)));
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .corner {
      animation: none;
    }
  }
</style>
