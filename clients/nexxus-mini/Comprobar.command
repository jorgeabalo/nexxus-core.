#!/bin/sh
cd "$(dirname "$0")" || exit 1
/usr/bin/python diagnose.py
printf '\nPulsa Intro para cerrar. '
read nexxus_answer
