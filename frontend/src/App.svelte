<script lang="ts">
  import { fade } from 'svelte/transition'
  import { booth } from './lib/booth'
  import Countdown from './screens/Countdown.svelte'
  import Idle from './screens/Idle.svelte'
  import Result from './screens/Result.svelte'

  let innerWidth = $state(1)
  let innerHeight = $state(1)
  let stageWidth = $state(1)
  let stageHeight = $state(1)

  const scale = $derived(Math.min(innerWidth / stageWidth, innerHeight / stageHeight))
</script>

<svelte:window bind:innerWidth bind:innerHeight />

<main style:scale bind:clientWidth={stageWidth} bind:clientHeight={stageHeight}>
  {#if $booth}
    {#if $booth.screen === 'idle'}
      <Idle message={$booth.message} fault={$booth.fault} />
    {:else if $booth.screen === 'result'}
      <Result state={$booth} />
    {:else}
      <Countdown remaining={$booth.remaining} />
    {/if}
    {#if $booth.screen === 'capturing'}
      <div class="flash" out:fade={{ duration: 300 }}></div>
    {/if}
  {/if}
</main>

<style>
  main {
    position: absolute;
    left: calc(50% - var(--stage-w) * 0.5px);
    top: calc(50% - var(--stage-h) * 0.5px);
    width: calc(var(--stage-w) * 1px);
    height: calc(var(--stage-h) * 1px);
    overflow: hidden;
  }

  .flash {
    position: absolute;
    inset: 0;
    background: #fff;
  }
</style>
