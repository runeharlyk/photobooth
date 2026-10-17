<script lang="ts">
  let { opacity }: { opacity: number } = $props()

  const pieces = [
    { left: 8, top: 12, angle: 30 },
    { left: 20, top: 72, angle: -20 },
    { left: 35, top: 20, angle: 60 },
    { left: 66, top: 14, angle: 10 },
    { left: 80, top: 66, angle: -45 },
    { left: 90, top: 30, angle: 80 },
    { left: 12, top: 46, angle: 15 },
    { left: 70, top: 84, angle: 40 },
    { left: 52, top: 90, angle: -65 },
    { left: 4, top: 86, angle: 50 },
    { left: 94, top: 50, angle: 10 },
    { left: 46, top: 6, angle: -30 },
  ]
</script>

<div class="confetti" style:opacity>
  {#each pieces as piece}
    <span style:left="{piece.left}%" style:top="{piece.top}%" style:transform="rotate({piece.angle}deg)"></span>
  {/each}
</div>

<style>
  .confetti {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }

  span {
    position: absolute;
    width: 24px;
    height: 8px;
    border-radius: 3px;
    background: var(--green);
  }
</style>
