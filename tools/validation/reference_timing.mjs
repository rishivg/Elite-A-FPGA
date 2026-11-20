// Observe the clock inside video.polltime's batch, without advancing any
// other peripheral or changing the original CPU's instruction scheduling.
export function observeVideoClock(video, initialCycle = 0) {
  let cycle = initialCycle;
  const poll = video.polltime.bind(video);
  video.polltime = (count) => {
    if (!Number.isInteger(count) || count < 0) throw Error("Invalid video clock batch");
    for (let i = 0; i < count; i++) {
      cycle++;
      poll(1);
    }
  };
  return {
    get cycle() {
      return cycle;
    },
    reset(value) {
      cycle = value;
    },
  };
}
