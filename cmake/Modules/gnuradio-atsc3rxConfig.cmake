find_package(PkgConfig)

PKG_CHECK_MODULES(PC_GR_ATSC3RX gnuradio-atsc3rx)

FIND_PATH(
    GR_ATSC3RX_INCLUDE_DIRS
    NAMES gnuradio/atsc3rx/api.h
    HINTS $ENV{ATSC3RX_DIR}/include
        ${PC_ATSC3RX_INCLUDEDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/include
          /usr/local/include
          /usr/include
)

FIND_LIBRARY(
    GR_ATSC3RX_LIBRARIES
    NAMES gnuradio-atsc3rx
    HINTS $ENV{ATSC3RX_DIR}/lib
        ${PC_ATSC3RX_LIBDIR}
    PATHS ${CMAKE_INSTALL_PREFIX}/lib
          ${CMAKE_INSTALL_PREFIX}/lib64
          /usr/local/lib
          /usr/local/lib64
          /usr/lib
          /usr/lib64
          )

include("${CMAKE_CURRENT_LIST_DIR}/gnuradio-atsc3rxTarget.cmake")

INCLUDE(FindPackageHandleStandardArgs)
FIND_PACKAGE_HANDLE_STANDARD_ARGS(GR_ATSC3RX DEFAULT_MSG GR_ATSC3RX_LIBRARIES GR_ATSC3RX_INCLUDE_DIRS)
MARK_AS_ADVANCED(GR_ATSC3RX_LIBRARIES GR_ATSC3RX_INCLUDE_DIRS)
