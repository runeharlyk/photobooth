<script lang="ts">
  import type { Snippet } from 'svelte'

  let { tilt, size, children }: { tilt: number; size: number; children: Snippet } = $props()
</script>

<span class="sticker" style:--tilt="{tilt}deg" style:--size="{size}px">{@render children()}</span>

<style>
  .sticker {
    display: inline-block;
    transform: rotate(var(--tilt));
    padding: 0.2em 0.6em;
    border: 0.12em solid var(--green);
    background: #111;
    box-shadow: 0.15em 0.18em 0 #0008;
    color: var(--green);
    font-family: 'Archivo Black', sans-serif;
    font-size: var(--size);
    letter-spacing: 0.03em;
    line-height: 1.2;
    white-space: nowrap;
  }
</style>
