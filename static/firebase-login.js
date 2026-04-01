'use strict';
import { initializeApp } from "https://www.gstatic.com/firebasejs/12.9.0/firebase-app.js";
import { getAuth, createUserWithEmailAndPassword, signInWithEmailAndPassword, signOut } from "https://www.gstatic.com/firebasejs/12.9.0/firebase-auth.js";

// My web app's firebase configuration
const firebaseConfig = {
    apiKey: "AIzaSyCDREnGV_cFIqNwB2nQDWq859S1haGskWw",
    authDomain: "paas-fastapi-fa3aa.firebaseapp.com",
    projectId: "paas-fastapi-fa3aa",
    storageBucket: "paas-fastapi-fa3aa.firebasestorage.app",
    messagingSenderId: "1055790212122",
    appId: "1:1055790212122:web:26040a357326a4788c1da3"

};

// Run setup and attach button handlers once the page has fully loaded
window.addEventListener("load", function () {
    const app = initializeApp(firebaseConfig);
    const auth = getAuth(app);
    // Show login box or sign-out button depending on current cookie
    updateUI(document.cookie);
    console.log("hello world load");

    // signup of a new user to firebase
    document.getElementById("sign-up").addEventListener('click', function () {
        const email = document.getElementById("email").value;
        const password = document.getElementById("password").value;

        createUserWithEmailAndPassword(auth, email, password)
            .then((userCredential) => {
                // we have created a user
                const user = userCredential.user;

                // get the id token for the user who just logged in and force a redirect to /
                user.getIdToken().then((token) => {
                    document.cookie = "token=" + token + ";path=/;SameSite=Strict";
                    window.location = "/";
                });
            })
            .catch((error) => {
                // issue for signup that we will drop to console
                console.log(error.code + error.message);
            });
    });

    // login of a user to firebase
    document.getElementById("login").addEventListener('click', function () {
        const email = document.getElementById("email").value;
        const password = document.getElementById("password").value;

        signInWithEmailAndPassword(auth, email, password)
            .then((userCredential) => {
                const user = userCredential.user;
                console.log("logged in");


                user.getIdToken().then((token) => {
                    document.cookie = "token=" + token + ";path=/;SameSite=Strict";
                    window.location = "/"
                });
            })
            .catch((error) => {
                console.log(error.code + error.message)
            });
    });
    document.getElementById("sign-out").addEventListener('click', function () {
        signOut(auth)
            .then((output) => {
                document.cookie = "token=; path=/; SameSite=Strict";
                window.location = "/";
            });
    });
});

// function that will update the UI for the user depending on if they are logged in or note by checking the passed in cookie
function updateUI(cookie) {
    var token = parseCookieToken(cookie);
    var loginBox = document.getElementById("login-box");
    var signOutBox = document.getElementById("sign-out-box");
    if (token.length > 0) {
        loginBox.hidden = true;
        signOutBox.hidden = false;
    } else {
        loginBox.hidden = false;
        signOutBox.hidden = true;
    }
}



function parseCookieToken(cookie) {

    var strings = cookie.split(';');
    for (let i = 0; i < strings.length; i++) {
        var temp = strings[i].split('=');
        if (temp[0].trim() === "token") {
            return temp[1].trim();
        }
    }

    return ""
}
