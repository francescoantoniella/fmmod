#pragma once
/**
 * iio_compat.hpp
 * Macro di compatibilità tra libiio v0.x e v1.x.
 * Includi PRIMA di qualsiasi header libiio.
 *
 * v0.x API: iio_channel_attr_write_longlong(ch, "name", val)
 * v1.x API: iio_attr_write_longlong(iio_channel_find_attr(ch, "name"), val)
 */
#ifdef USE_LIBIIO
#include <iio.h>

// Rileva versione: LIBIIO_VERSION_MAJOR è definita in iio.h da v1.0+
#ifndef LIBIIO_VERSION_MAJOR
  #define LIBIIO_VERSION_MAJOR 0
#endif

#if LIBIIO_VERSION_MAJOR >= 1
  // ---- libiio v1.x -------------------------------------------------------
  #define IIO_CH_ATTR_WRITE_LL(ch, name, val)                              \
      do {                                                                   \
          const struct iio_attr* _a = iio_channel_find_attr((ch), (name));  \
          if (_a) iio_attr_write_longlong(_a, (val));                        \
      } while (0)

  #define IIO_CH_ATTR_WRITE_DBL(ch, name, val)                             \
      do {                                                                   \
          const struct iio_attr* _a = iio_channel_find_attr((ch), (name));  \
          if (_a) iio_attr_write_double(_a, (val));                          \
      } while (0)

  #define IIO_CH_ATTR_WRITE_STR(ch, name, val)                             \
      do {                                                                   \
          const struct iio_attr* _a = iio_channel_find_attr((ch), (name));  \
          if (_a) iio_attr_write_string(_a, (val));                          \
      } while (0)
#else
  // ---- libiio v0.x -------------------------------------------------------
  #define IIO_CH_ATTR_WRITE_LL(ch, name, val)                              \
      iio_channel_attr_write_longlong((ch), (name), (val))

  #define IIO_CH_ATTR_WRITE_DBL(ch, name, val)                             \
      iio_channel_attr_write_double((ch), (name), (val))

  #define IIO_CH_ATTR_WRITE_STR(ch, name, val)                             \
      iio_channel_attr_write_string((ch), (name), (val))
#endif // LIBIIO_VERSION_MAJOR

#endif // USE_LIBIIO
