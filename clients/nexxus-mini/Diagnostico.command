#!/bin/sh
cd "$(dirname "$0")" || exit 1
/usr/bin/python diagnose.py --online
printf '\nPulsa Intro para cerrar.\n'
read answer
