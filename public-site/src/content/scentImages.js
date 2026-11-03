/**
 * The right-sized copy of a scent render (files named like `name-s.webp` sit beside each original):
 * 's' is 360 px tall (the rail, the hover card), 'm' is 760 px tall (the
 * catalog cards). The full-size file stays for the big collection tiles.
 */
export const thumb = (src, size) => src.replace(/\.webp$/, `-${size}.webp`);
