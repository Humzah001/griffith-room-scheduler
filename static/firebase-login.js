'use strict';
import { initializeApp } from "https://www.gstatic.com/firebasejs/12.9.0/firebase-app.js";
import { getAuth, createUserWithEmailAndPassword, signInWithEmailAndPassword, signOut } from "https://www.gstatic.com/firebasejs/12.9.0/firebase-auth.js";

const firebaseConfig = {
    apiKey: "AIzaSyCDREnGV_cFIqNwB2nQDWq859S1haGskWw",
    authDomain: "paas-fastapi-fa3aa.firebaseapp.com",
    projectId: "paas-fastapi-fa3aa",
    storageBucket: "paas-fastapi-fa3aa.firebasestorage.app",
    messagingSenderId: "1055790212122",
    appId: "1:1055790212122:web:26040a357326a4788c1da3"
};

function firebaseAuthErrorMessage(error) {
    var code = error && error.code ? error.code : "";
    var map = {
        "auth/wrong-password": "Wrong email or password.",
        "auth/invalid-credential": "Wrong email or password.",
        "auth/user-not-found": "Wrong email or password.",
        "auth/invalid-email": "Please enter a valid email address.",
        "auth/user-disabled": "This account has been disabled.",
        "auth/too-many-requests": "Too many attempts. Try again later.",
        "auth/email-already-in-use": "That email is already registered. Try signing in.",
        "auth/weak-password": "Password should be at least 6 characters.",
        "auth/operation-not-allowed": "Email/password sign-in is not enabled in Firebase.",
        "auth/network-request-failed": "Network error. Check your connection.",
    };
    return map[code] || (error && error.message) || "Something went wrong. Please try again.";
}

function showAuthError(error) {
    window.alert(firebaseAuthErrorMessage(error));
}

window.addEventListener("load", function () {
    var app = initializeApp(firebaseConfig);
    var auth = getAuth(app);

    // Guest HTML (server did not validate the session): never hide the login form
    // based only on a stale cookie — that produced the empty "bridge" screen.
    var serverAuth = document.body.getAttribute("data-server-auth") || "";
    if (serverAuth === "1") {
        updateUI(document.cookie);
    }

    var guestForm = document.getElementById("firebase-guest-form");
    if (guestForm) {
        guestForm.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var loginBtn = document.getElementById("login");
            if (loginBtn) {
                loginBtn.click();
            }
        });
    }

    var signUpBtn = document.getElementById("sign-up");
    if (signUpBtn) {
        signUpBtn.addEventListener("click", function () {
            var emailEl = document.getElementById("email");
            var passEl = document.getElementById("password");
            var email = emailEl ? emailEl.value.trim() : "";
            var password = passEl ? passEl.value : "";
            if (!email || !password) {
                window.alert("Please enter email and password.");
                return;
            }
            createUserWithEmailAndPassword(auth, email, password)
                .then(function (cred) {
                    return cred.user.getIdToken();
                })
                .then(function (token) {
                    setTokenCookie(token);
                    redirectAfterAuth();
                })
                .catch(function (error) {
                    showAuthError(error);
                });
        });
    }

    var loginBtn = document.getElementById("login");
    if (loginBtn) {
        loginBtn.addEventListener("click", function () {
            var emailEl = document.getElementById("email");
            var passEl = document.getElementById("password");
            var email = emailEl ? emailEl.value.trim() : "";
            var password = passEl ? passEl.value : "";
            if (!email || !password) {
                window.alert("Please enter email and password.");
                return;
            }
            signInWithEmailAndPassword(auth, email, password)
                .then(function (cred) {
                    return cred.user.getIdToken();
                })
                .then(function (token) {
                    setTokenCookie(token);
                    redirectAfterAuth();
                })
                .catch(function (error) {
                    showAuthError(error);
                });
        });
    }

    var signOutBtn = document.getElementById("sign-out");
    if (signOutBtn) {
        signOutBtn.addEventListener("click", function () {
            signOut(auth).then(function () {
                clearTokenCookie();
                redirectToLogin();
            });
        });
    }
});

function updateUI(cookie) {
    var token = parseCookieToken(cookie);
    var loginBox = document.getElementById("login-box");
    var signOutBox = document.getElementById("sign-out-box");
    if (!loginBox || !signOutBox) {
        return;
    }
    if (token.length > 0) {
        loginBox.hidden = true;
        signOutBox.hidden = false;
    } else {
        loginBox.hidden = false;
        signOutBox.hidden = true;
    }
}

function setTokenCookie(token) {
    document.cookie = "token=" + encodeURIComponent(token) + ";path=/;SameSite=Lax";
}

function clearTokenCookie() {
    document.cookie = "token=;path=/;SameSite=Lax;Max-Age=0";
}

function redirectAfterAuth() {
    var base = document.body.getAttribute("data-after-login") || "/system";
    var sep = base.indexOf("?") === -1 ? "?" : "&";
    window.location.assign(base + sep + "_=" + Date.now());
}

function redirectToLogin() {
    window.location.assign("/?_=" + Date.now());
}

function parseCookieToken(cookie) {
    var strings = cookie.split(";");
    for (var i = 0; i < strings.length; i++) {
        var part = strings[i].trim();
        if (part.indexOf("token=") === 0) {
            var raw = part.slice("token=".length);
            try {
                return decodeURIComponent(raw);
            } catch (e) {
                return raw;
            }
        }
    }
    return "";
}
