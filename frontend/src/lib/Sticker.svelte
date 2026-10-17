<script lang="ts">
  import type { Snippet } from 'svelte'

  let {
    tilt,
    size,
    border,
    shadow,
    children,
  }: { tilt: number; size: number; border: number; shadow: [number, number]; children: Snippet } = $props()
</script>

<span
  class="sticker"
  style:--tilt="{tilt}deg"
  style:--size="{size}px"
  style:--border="{border}px"
  style:--shadow-x="{shadow[0]}px"
  style:--shadow-y="{shadow[1]}px">{@render children()}</span
>

<style>
  .sticker {
    display: inline-block;
    transform: rotate(var(--tilt));
    padding: 0.2em 0.6em;
    border: var(--border) solid var(--green);
    background: #111;
    box-shadow: var(--shadow-x) var(--shadow-y) 0 #0008;
    color: var(--green);
    font-family: 'Archivo Black', sans-serif;
    font-size: var(--size);
    letter-spacing: 0.03em;
    line-height: 1.2;
    text-align: center;
    white-space: nowrap;
  }
</style>
